import sys
import time

sys.path.insert(0, ".")

print("Loading LLM and measuring warm-up...")
from amigo.core.llm_agent import warmup_router_prefix, get_agent_action

t_warmup = warmup_router_prefix()
print(f"Warmup time: {t_warmup:.2f} s")

# Now simulate user's first real turn
t0 = time.perf_counter()
res = get_agent_action("what time is it right now")
t_first_turn = (time.perf_counter() - t0) * 1000

print(f"First user turn latency: {t_first_turn:.1f} ms")
print(f"Result action: {res}")

