"""
Multiply tool for OpenAI Realtime API
"""


def get_multiply_tool_definition() -> dict:
    """Returns the tool definition for the multiply function"""
    return {
        "type": "function",
        "name": "multiply",
        "description": "Multiplies two numbers together. Useful for mathematical calculations.",
        "parameters": {
            "type": "object",
            "properties": {
                "a": {
                    "type": "number",
                    "description": "The first number to multiply",
                },
                "b": {
                    "type": "number",
                    "description": "The second number to multiply",
                },
            },
            "required": ["a", "b"],
        },
    }


async def execute_multiply(a: float, b: float) -> dict:
    """
    Executes the multiply function

    Args:
        a: First number
        b: Second number

    Returns:
        Dictionary with the result
    """
    result = a * b
    return {
        "result": result,
        "calculation": f"{a} × {b} = {result}",
    }
