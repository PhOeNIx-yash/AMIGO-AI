from amigo.core.llm_agent import get_agent_actions
import amigo.core.llm_agent as llm_agent

original_query_local_llm = llm_agent.query_local_llm
def debug_query_local_llm(messages, *args, **kwargs):
    print('=== MESSAGES SENT TO LLM ===')
    for i, msg in enumerate(messages):
        content = msg['content']
        print(f'Message {i}: {msg["role"]} - {content}')
    return original_query_local_llm(messages, *args, **kwargs)

llm_agent.query_local_llm = debug_query_local_llm

result = get_agent_actions('play it again', conversation_history=[{'user': 'play believer by imagine dragons', 'assistant': 'Playing Believer', 'tool': 'play_youtube'}])
print('=== RESULT ===')
print(result)