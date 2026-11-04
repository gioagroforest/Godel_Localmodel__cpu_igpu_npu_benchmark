"""How closely does each compressed recipe follow the original weights?

Feeds real proofs through the uncompressed PyTorch model and through each recipe, and compares
the next-token predictions position by position. Greedy decoding follows these predictions, so
top-1 agreement is the fraction of steps where a recipe would pick the same token as the original.
The official plain Qwen3 is a negative control: it has different weights, so it should score lower.

  accuracy.py reference          the PyTorch run, saved to lab/accuracy_reference.npz
  accuracy.py recipe NAME DEVICE one recipe, appended to lab/accuracy.jsonl
  accuracy.py all                everything above, each in its own guarded process
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

import bench

LAB = Path(__file__).parent / "lab"
MODELS = bench.ROOT / "models"
REFERENCE = LAB / "accuracy_reference.npz"
RESULTS = LAB / "accuracy.jsonl"
LEMMAS = ["task_a", "task_b", "task_c"]
MAX_TOKENS = 1024
RECIPES = [("goedel-ov-int4-cw", "CPU"), ("goedel-ov-int4-cw", "GPU"), ("goedel-ov-int4-cw-i8sym", "CPU"),
           ("goedel-ov-int4-gq128", "CPU"), ("goedel-ov-int4-asym-gq128", "CPU"),
           ("goedel-ov-nf4-cw", "CPU"), ("Qwen3-8B-int4-cw-ov-official", "CPU")]


def sequences():
    """Prompt and proof tokens, from the replies that proved each lemma in the first benchmark."""
    import openvino_genai as genai
    tokenizer = genai.Tokenizer(str(MODELS / "goedel-ov-int4-cw"))
    encode = lambda text: tokenizer.encode(text, add_special_tokens=False).input_ids.data[0].tolist()
    replies = {}
    for line in (Path(__file__).parent / "out" / "prove.jsonl").open(encoding="utf-8"):
        row = json.loads(line)
        if row["config"] == "B-llama-CPU" and row["verdict"] == "PROVED":
            replies[row["lemma"]] = row["reply"]
    result = {}
    for name in LEMMAS:
        prompt = encode(bench.lemma_prompt(name))
        result[name] = (prompt + encode(replies[name]))[:MAX_TOKENS], len(prompt)
    return result


def top5(logits):
    return np.argpartition(-logits, 5, axis=-1)[..., :5]


def run_reference():
    import torch
    from transformers import AutoModelForCausalLM
    torch.set_num_threads(20)
    model = AutoModelForCausalLM.from_pretrained(MODELS / "Goedel-Prover-V2-8B-hf", dtype=torch.bfloat16,
                                                 low_cpu_mem_usage=True)
    model.eval()
    saved = {}
    for name, (tokens, prompt) in sequences().items():
        start = time.time()
        with torch.inference_mode():
            logits = model(torch.tensor([tokens]), use_cache=False).logits[0].float().numpy()
        ranked = np.sort(logits, -1)
        saved[f"{name}_top1"] = logits.argmax(-1).astype(np.int32)
        saved[f"{name}_top5"] = top5(logits).astype(np.int32)
        # The gap in log-probability between the two best tokens: logits differ by the same amount.
        saved[f"{name}_margin"] = ranked[:, -1] - ranked[:, -2]
        print(f"REFERENCE {name}: {len(tokens)} tokens, {time.time() - start:.0f} s", flush=True)
    np.savez(REFERENCE, **saved)


def run_recipe(ir, device):
    import openvino as ov
    reference = np.load(REFERENCE)
    props = {"INFERENCE_NUM_THREADS": 20} if device == "CPU" else {}
    compiled = ov.Core().compile_model(str(MODELS / ir / "openvino_model.xml"), device, props)
    for name, (tokens, prompt) in sequences().items():
        request = compiled.create_infer_request()
        ids = np.array([tokens], np.int64)
        request.set_tensor("input_ids", ov.Tensor(ids))
        request.set_tensor("attention_mask", ov.Tensor(np.ones_like(ids)))
        request.set_tensor("position_ids", ov.Tensor(np.arange(ids.shape[1], dtype=np.int64)[None]))
        request.set_tensor("beam_idx", ov.Tensor(np.array([0], np.int32)))
        request.infer()
        logits = request.get_tensor("logits").data[0]
        generated = np.arange(len(tokens) - 1) >= prompt - 1  # predictions of generated tokens only
        want, want5 = reference[f"{name}_top1"][:-1], reference[f"{name}_top5"][:-1]
        got, got5 = logits.argmax(-1)[:-1], top5(logits)[:-1]
        same = got == want
        overlap = np.array([len(set(a) & set(b)) / 5 for a, b in zip(got5, want5)])
        confident = reference[f"{name}_margin"][:-1] > 1.0  # the original clearly prefers one token
        record = {"ir": ir, "device": device, "lemma": name, "positions": int(generated.sum()),
                  "top1": round(float(same[generated].mean()), 4),
                  "top5_overlap": round(float(overlap[generated].mean()), 4),
                  "top1_confident": round(float(same[generated & confident].mean()), 4)}
        with RESULTS.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record) + "\n")
        print(record, flush=True)


def guarded(arguments, limit):
    code = bench.watch(subprocess.Popen([sys.executable, __file__, *arguments]), limit, 5.0)
    print(f"ACCURACY {' '.join(arguments)}: {code}", flush=True)
    return code == 0


def main():
    command = sys.argv[1]
    if command == "reference":
        run_reference()
    elif command == "recipe":
        run_recipe(sys.argv[2], sys.argv[3])
    else:
        bench.wait_for_memory()
        if not REFERENCE.exists() and not guarded(["reference"], 3600):
            print("ACCURACY_STOPPED no reference", flush=True)
            return
        for ir, device in RECIPES:
            bench.wait_for_memory()
            guarded(["recipe", ir, device], 1800)
        print("ACCURACY_DONE", flush=True)


if __name__ == "__main__":
    main()
