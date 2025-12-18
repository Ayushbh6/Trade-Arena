import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# We use the OpenAI-compatible client for OpenRouter
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

def get_completion(messages, model="x-ai/grok-code-fast-1", **kwargs):
    """
    Generic wrapper for OpenRouter completions.
    Supports 'response_format', 'tools', 'tool_choice', etc. via kwargs.
    
    Returns:
        - str: Content string if no tools are used.
        - message object: If tools are used (so caller can access tool_calls).
    """
    api_kwargs = {
        "model": model,
        "messages": messages,
    }
    
    # Merge optional args like response_format, tools, tool_choice
    api_kwargs.update(kwargs)

    try:
        response = client.chat.completions.create(**api_kwargs)
        message = response.choices[0].message
        
        # If tools were requested, return the full message so we can parse tool_calls
        if "tools" in kwargs:
            return message
        
        # Otherwise, strictly return content string (backward compatibility)
        return message.content
    except Exception as e:
        print(f"Error calling OpenRouter: {e}")
        return None
