"""Sampling policy for page evaluations; ordinary business configuration is untouched."""

SAMPLING = {'temperature': 0.0, 'top_p': 1.0, 'seed': 42,
            'frequency_penalty': 0.0, 'presence_penalty': 0.0}


def evaluation_llm_config():
    from tools.llm_client import get_section
    return {**get_section('llm_config'), **SAMPLING, 'enable_thinking': False}


def evaluation_call_llm(prompt, parameters=None, system_prompt=None):
    from tools.llm_client import call_llm, DEFAULT_SYSTEM_PROMPT
    return call_llm(prompt, parameters, system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
                    config=evaluation_llm_config())
