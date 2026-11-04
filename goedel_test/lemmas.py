"""Placeholder tasks, so the scripts can run. Replace them with your own Lean 4 statements.

The statements used for the published results are not included, so rerunning with these
gives different prompts, token counts and proof results. Each statement stops before `:= by`.
"""

LEMMAS = [
    ("task_a", 1, """theorem task_a (a b : ℕ) : a + b = b + a"""),
    ("task_b", 1, """theorem task_b (a b c : ℕ) : a + (b + c) = (a + b) + c"""),
    ("task_c", 1, """theorem task_c (a b : ℕ) (h : a ≤ b) : a < b + 1"""),
    ("task_d", 1, """theorem task_d (n : ℕ) : n * 0 = 0"""),
    ("task_e", 1, """theorem task_e (a b : ℤ) : (a + b) ^ 2 = a ^ 2 + 2 * a * b + b ^ 2"""),
    ("task_f", 1, """theorem task_f (x : ℤ) : 0 ≤ x ^ 2"""),
    ("task_s", 1, """theorem task_s (a b c : ℕ) (h : a ≤ b) : a + c ≤ b + c"""),
]
