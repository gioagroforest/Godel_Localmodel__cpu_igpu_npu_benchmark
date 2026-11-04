"""NPU prefill with decode on another device, as two graphs handing over the KV cache.

The NPU runs a static stateless copy of the model, chunk by chunk, keeping the KV cache in host
memory. The cache is then loaded into the decode device's stateful model as its variable state.
Weights are held twice, once per device, which is the method's known memory cost.
"""
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import openvino as ov
import openvino_genai as genai
from openvino import _offline_transformations as offline
from openvino.preprocess import PrePostProcessor

import bench

NPU_PROPS = {
    "NPU_USE_NPUW": "YES", "NPUW_DEVICES": "NPU", "NPUW_FOLD": "YES",
    "NPUW_DCOFF_TYPE": "f16", "NPUW_DCOFF_SCALE": "YES", "NPUW_WEIGHTS_BANK": "shared",
    "NPUW_FUNCALL_ASYNC": "YES", "NPUW_ONLINE_PIPELINE": "REP",
    "NPUW_ONLINE_ISOLATE": "ATTN", "NPUW_ONLINE_KEEP_BLOCK_SIZE": "4",
    "NPU_COMPILATION_MODE_PARAMS": "compute-layers-with-higher-precision=Sqrt,Power,ReduceMean,Add_RMSNorm",
}


def last_logit_only(model):
    """Run the language head on the last position only, as prefill needs nothing else."""
    head = model.output("logits").get_node().input_value(0).get_node()
    if head.get_type_name() != "MatMul":
        raise RuntimeError(f"unexpected head {head.get_type_name()}")
    source = head.input_value(0)
    last = ov.opset13.slice(source, np.array([-1]), np.array([2**31 - 1]), np.array([1]), np.array([1]))
    head.input(0).replace_source_output(last.output(0))
    model.validate_nodes_and_infer_types()


def redirect_present(model):
    """Output only the new KV positions, so prefill doesn't copy the whole cache every chunk."""
    order = []
    for index, output in enumerate(model.outputs):
        name = output.get_any_name()
        if not name.startswith("present"):
            continue
        result = output.get_node()
        concat = result.input_value(0).get_node()
        if concat.get_type_name() != "Concat":
            raise RuntimeError(f"unexpected {concat.get_type_name()} before {name}")
        result.input(0).replace_source_output(concat.input_value(1))
        order.append((name.replace("present", "past_key_values"), index))
    model.validate_nodes_and_infer_types()
    return order


def kv_as_f16(model, order):
    """The host keeps the cache in 16 bits, the precision the devices compute in."""
    process = PrePostProcessor(model)
    for name, index in order:
        process.input(name).tensor().set_element_type(ov.Type.f16)
        process.output(index).tensor().set_element_type(ov.Type.f16)
    return process.build()


def decode_props(device, threads, cache):
    props = {} if device == "CPU" else {"CACHE_DIR": str(cache)}  # a CPU cache saves little and fills the disk
    if device == "GPU":
        props["KV_CACHE_PRECISION"] = "f16"  # set_state is refused on the compressed cache
    if device == "CPU" and threads:
        props["INFERENCE_NUM_THREADS"] = threads
    return props


def precompile(xml, device, threads, cache):
    """Fill the decode device's cache alone: compiling beside the NPU graph runs out of memory."""
    core = ov.Core()
    core.compile_model(str(xml), device, decode_props(device, threads, cache))


class HybridSession:
    def __init__(self, ir, device, threads, chunk, max_prompt, cache_tag):
        folder = bench.ROOT / "models" / ir
        xml = folder / "openvino_model.xml"
        cache = bench.OV_CACHE / f"{cache_tag}_{device}"  # shared with the native runs of the same setup
        subprocess.run([sys.executable, __file__, str(xml), device, str(threads or 0), str(cache)], check=True)
        self.tokenizer = genai.Tokenizer(str(folder))
        self.chunk, self.past = chunk, max_prompt
        total = max_prompt + chunk
        core = ov.Core()

        model = core.read_model(xml)
        offline.stateful_to_stateless_transformation(model)
        last_logit_only(model)
        shapes = {}
        for port in model.inputs:
            name = port.get_any_name()
            if name.startswith("past_key_values"):
                shapes[name] = [1, 8, self.past, 128]
            else:
                shapes[name] = [1, total] if name == "attention_mask" else [1, chunk]
        model.reshape(shapes)
        self.order = redirect_present(model)
        self.layers = [name for name, _ in self.order]
        model = kv_as_f16(model, self.order)
        prefill = core.compile_model(model, "NPU", NPU_PROPS)
        del model
        self.prefill = prefill.create_infer_request()

        decoder = core.compile_model(str(xml), device, decode_props(device, threads, cache))
        self.decode = decoder.create_infer_request()

    def run_prefill(self, ids):
        """Returns the last logits and the KV cache, each tensor holding only real positions."""
        if len(ids) > self.past:
            raise ValueError(f"prompt of {len(ids)} tokens exceeds the prefill's {self.past}")
        past = {name: np.zeros((1, 8, self.past, 128), np.float16) for name in self.layers}
        total, chunk = self.past + self.chunk, self.chunk
        filled, logits = 0, None
        for start in range(0, len(ids), chunk):
            real = ids[start:start + chunk]
            count = len(real)
            input_ids = np.zeros((1, chunk), np.int64)
            positions = np.zeros((1, chunk), np.int64)
            mask = np.zeros((1, total), np.int64)
            input_ids[0, -count:] = real  # a partial chunk is padded on the left
            positions[0, -count:] = np.arange(start, start + count)
            mask[0, self.past - filled:self.past] = 1
            mask[0, -count:] = 1
            self.prefill.set_tensor("input_ids", ov.Tensor(input_ids))
            self.prefill.set_tensor("attention_mask", ov.Tensor(mask))
            self.prefill.set_tensor("position_ids", ov.Tensor(positions))
            for name in self.layers:
                self.prefill.set_tensor(name, ov.Tensor(past[name], shared_memory=True))
            self.prefill.infer()
            logits = self.prefill.get_tensor("logits").data[0, -1].copy()
            for name, index in self.order:
                new = self.prefill.get_output_tensor(index).data[:, :, -count:, :]
                buffer = past[name]
                buffer[:, :, :self.past - count, :] = buffer[:, :, count:, :]
                buffer[:, :, -count:, :] = new
            filled += count
        return logits, {name: buffer[:, :, -filled:, :] for name, buffer in past.items()}

    def step(self, token, length):
        self.decode.set_tensor("input_ids", ov.Tensor(np.array([[token]], np.int64)))
        self.decode.set_tensor("attention_mask", ov.Tensor(np.ones((1, length + 1), np.int64)))
        self.decode.set_tensor("position_ids", ov.Tensor(np.array([[length]], np.int64)))
        self.decode.set_tensor("beam_idx", ov.Tensor(np.array([0], np.int32)))
        self.decode.infer()
        return int(self.decode.get_tensor("logits").data[0, -1].argmax())

    def generate(self, prompt, max_tokens, ignore_eos):
        ids = self.tokenizer.encode(prompt, add_special_tokens=False).input_ids.data[0].tolist()
        eos = {151645, 151643}  # <|im_end|> and <|endoftext|>, from the model's generation config
        wall_start, start = time.time(), time.perf_counter()
        logits, cache = self.run_prefill(ids)
        token = int(logits.argmax())
        tokens, stamps = [token], [time.perf_counter() - start]

        self.decode.reset_state()
        self.step(token, 0)  # the iGPU ignores a loaded state until the model has run once
        for state in self.decode.query_state():
            state.state = ov.Tensor(cache[state.name.split("present")[0]])
        length = len(ids)
        while len(tokens) < max_tokens and (ignore_eos or token not in eos):
            token = self.step(token, length)
            tokens.append(token)
            stamps.append(time.perf_counter() - start)
            length += 1
        wall_end = time.time()
        finish = "length" if len(tokens) >= max_tokens else "stop"
        text = self.tokenizer.decode(tokens)
        return bench.Result(stamps, tokens, text, len(ids), finish, wall_start, wall_end)

    def peak_gb(self):
        import psutil
        return psutil.Process().memory_info().peak_wset / 2**30

    def close(self):
        del self.prefill, self.decode


if __name__ == "__main__":
    xml_path, target, thread_count, cache_dir = sys.argv[1:5]
    precompile(Path(xml_path), target, int(thread_count), Path(cache_dir))
