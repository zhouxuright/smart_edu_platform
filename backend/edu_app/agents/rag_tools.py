import json
import logging
import asyncio
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, SystemMessage
from edu_app.agents.base import get_llm

async def analyze_query(message: str, history: list) -> dict:
    """
    Analyze the user's message and history to decide if RAG is needed,
    and if so, generate an optimized search query.
    
    Returns:
        dict: {"needs_rag": bool, "search_query": str, "reason": str}
    """
    try:
        llm = get_llm(temperature=0.0, max_tokens=256)
        
        # Check if LLM is a fallback (RunnableLambda)
        # If it's a fallback, we can't really do smart analysis, so we default to a heuristic or just False.
        # However, the fallback in get_llm returns a RunnableLambda that returns a string.
        # We can check if it's ChatOpenAI or not.
        if not hasattr(llm, "invoke") and not hasattr(llm, "ainvoke"):
             # Should not happen given get_llm implementation, but safe guard
             return {"needs_rag": False, "search_query": "", "reason": "LLM invalid"}
             
        # Simple heuristic check for fallback: if get_llm returns the dummy lambda
        # But actually, get_llm returns a Runnable. 
        # Let's just try to use it. If it's the fallback lambda, it will return the fallback text, which won't be JSON.
        
        system_prompt = """You are an expert intent classifier and query optimizer for an educational AI assistant.
Your task is to analyze the User's latest input and the conversation history to decide if the system should retrieve external knowledge (RAG) from the knowledge base.

Rules for "needs_rag":
- TRUE if the user asks for factual information, definitions, concepts, explanations of specific topics, or "how-to" guides (e.g., "What is the Pythagorean theorem?", "Explain photosynthesis", "How to use this system?").
- FALSE if the user engages in casual chat (e.g., "Hello", "Thank you"), asks for personal advice/opinion without facts, or refers to purely internal context that doesn't need external lookup.

Rules for "search_query":
- If needs_rag is TRUE, rewrite the user's input into a concise, standalone search query (e.g., convert "How does it work?" based on history "Pythagoras" -> "Pythagorean theorem application").
- If needs_rag is FALSE, leave it empty.

Output must be valid JSON matching this structure:
{
    "needs_rag": boolean,
    "search_query": string or null,
    "reason": string
}
"""
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "History: {history}\nUser Input: {input}")
        ])
        
        # Prepare history string
        if isinstance(history, list):
            hist_str = "\n".join([f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in history[-3:]])
        else:
            hist_str = str(history)

        chain = prompt | llm
        
        try:
            logging.info(f"Analyzing query intent: {message[:50]}...")
            response = await asyncio.wait_for(chain.ainvoke({
                "history": hist_str,
                "input": message
            }), timeout=10.0) # 10s timeout for intent analysis
        except asyncio.TimeoutError:
            logging.warning("RAG Router timed out. Defaulting to NO RAG.")
            return {"needs_rag": False, "search_query": "", "reason": "Timeout"}
        except Exception as e:
            logging.warning(f"RAG Router LLM error: {e}")
            return {"needs_rag": False, "search_query": "", "reason": f"LLM Error: {e}"}
        
        if hasattr(response, "content"):
            content = response.content
        elif isinstance(response, str):
            content = response
        else:
            content = str(response)

        # Handle potential fallback text
        if "目标：" in content and "思考：" in content:
            # This is fallback text, no valid JSON
            return {"needs_rag": False, "search_query": "", "reason": "Fallback mode"}
            
        # Clean content to ensure JSON
        content = content.strip()
        if content.startswith("```json"):
            content = content.split("```json")[1].split("```")[0].strip()
        elif content.startswith("```"):
            content = content.split("```")[1].split("```")[0].strip()
            
        try:
            result = json.loads(content)
            needs_rag = bool(result.get("needs_rag", False))
            search_q = str(result.get("search_query", "") or "").strip()
            
            expanded_info = {}
            if needs_rag and search_q:
                try:
                    from edu_app.services.advanced_rag_pipeline import advanced_rag_pipeline
                    expanded_info = await advanced_rag_pipeline.generate_hyde_and_multi_query(search_q)
                except Exception as ex:
                    logging.warning(f"Advanced RAG HyDE Expansion failed: {ex}")
                    expanded_info = {"expanded_queries": [search_q], "hypothetical_doc": search_q}
            
            # Normalize keys with Advanced RAG payload
            return {
                "needs_rag": needs_rag,
                "search_query": search_q,
                "reason": str(result.get("reason", "") or ""),
                "expanded_queries": expanded_info.get("expanded_queries", [search_q] if search_q else []),
                "hypothetical_doc": expanded_info.get("hypothetical_doc", search_q)
            }
        except json.JSONDecodeError:
            logging.warning(f"RAG Router JSON decode failed. Content: {content[:100]}...")
            needs_rag = len(message) > 10 and not message.lower() in ["hi", "hello", "thanks"]
            return {"needs_rag": needs_rag, "search_query": message if needs_rag else "", "reason": "JSON Parse Error", "expanded_queries": [message], "hypothetical_doc": message}
            
    except Exception as e:
        logging.error(f"RAG Router failed: {e}")
        # Fail safe: don't use RAG
        return {"needs_rag": False, "search_query": "", "reason": f"Error: {e}"}
