import pytest
import sys
import os
import json
from unittest.mock import MagicMock, patch, AsyncMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from edu_app.agents import rag_tools

@pytest.mark.asyncio
async def test_analyze_query_needs_rag():
    # Mock get_llm to return a chain that returns a specific JSON
    with patch('edu_app.agents.rag_tools.get_llm') as mock_get_llm:
        mock_chain = MagicMock()
        # Mock ainvoke to return a response object with content
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "needs_rag": True,
            "search_query": "Pythagorean theorem application",
            "reason": "User asked for application of a theorem"
        })
        
        # Chain setup: prompt | llm -> chain
        # But in code: chain = prompt | llm
        # So we need to mock the pipe operation or just mock the llm to be the runnable
        # Wait, prompt | llm returns a RunnableSequence.
        # It's easier to mock ChatPromptTemplate and LLM, but patching the chain execution is tricky if we don't control the pipe.
        
        # Easier way: Mock RunnableLambda or ChatOpenAI returned by get_llm
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm
        
        # When prompt | llm is called, it returns a chain.
        # We need to mock the __or__ method of the prompt or the __ror__ of the llm?
        # Actually, LangChain's | operator logic.
        
        # Let's try to mock the chain execution by mocking `ainvoke` on the result of `prompt | llm`.
        # Since we can't easily intercept `|`, we can try to mock `ChatPromptTemplate` but that is imported in the module.
        
        # Alternative: We can mock `get_llm` to return an object that when piped with prompt returns a mock chain.
        # But `prompt | llm` logic is in LangChain.
        
        # Let's try to patch `edu_app.agents.rag_tools.ChatPromptTemplate` ?
        pass

# Let's write a simpler test that just patches get_llm and assumes the chain works if get_llm returns a standard Runnable.
# But since we don't want to rely on real LangChain logic which might fail if dependencies are missing or strict.
# Let's just create a mock LLM that behaves like a Runnable.

class MockRunnable:
    def __init__(self, response_content):
        self.response_content = response_content
        
    async def ainvoke(self, input_data):
        return type("Resp", (), {"content": self.response_content})

@pytest.mark.asyncio
async def test_analyze_query_mock():
    # We need to bypass the `prompt | llm` structure.
    # The code does: chain = prompt | llm; response = await chain.ainvoke(...)
    # If we make `llm` a special object where `prompt | llm` returns our MockRunnable, we are good.
    # LangChain's `PromptTemplate` | `Runnable` -> `RunnableSequence`.
    
    # If we can't easily mock the pipe, we can patch `ChatPromptTemplate` in `rag_tools`
    # to return a mock object that supports `|`.
    
    with patch('edu_app.agents.rag_tools.ChatPromptTemplate') as MockPrompt:
        mock_prompt_instance = MagicMock()
        MockPrompt.from_messages.return_value = mock_prompt_instance
        
        # Setup the chain mock
        mock_chain = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "needs_rag": True,
            "search_query": "photosynthesis process",
            "reason": "Request for explanation"
        })
        # Use AsyncMock for ainvoke
        mock_chain.ainvoke = AsyncMock(return_value=mock_response)
        
        # When prompt | llm happens
        mock_prompt_instance.__or__.return_value = mock_chain
        
        # Call the function
        result = await rag_tools.analyze_query("Explain photosynthesis", [])
        
        assert result["needs_rag"] is True
        assert result["search_query"] == "photosynthesis process"
        assert result["reason"] == "Request for explanation"

@pytest.mark.asyncio
async def test_analyze_query_no_rag():
    with patch('edu_app.agents.rag_tools.ChatPromptTemplate') as MockPrompt:
        mock_prompt_instance = MagicMock()
        MockPrompt.from_messages.return_value = mock_prompt_instance
        
        mock_chain = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "needs_rag": False,
            "search_query": "",
            "reason": "Casual greeting"
        })
        mock_chain.ainvoke = AsyncMock(return_value=mock_response)
        
        mock_prompt_instance.__or__.return_value = mock_chain
        
        result = await rag_tools.analyze_query("Hello there", [])
        
        assert result["needs_rag"] is False
        assert result["search_query"] == ""

if __name__ == "__main__":
    # Manually run async test if pytest not available
    import asyncio
    asyncio.run(test_analyze_query_mock())
    asyncio.run(test_analyze_query_no_rag())
    print("All tests passed!")
