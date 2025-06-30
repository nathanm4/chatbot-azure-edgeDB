# nodes.py
import re
import os
import ast
import urllib.parse
from langchain_community.utilities import SQLDatabase
from set_api_keys import *
from prompts import *
from states import *
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_core.prompts import ChatPromptTemplate
from typing import Literal


def set_variables():
    set_env("GROQ_API_KEY")
    set_env("LANGCHAIN_API_KEY")
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = "sql-llm-agent-tracker"

set_variables()


def load_db(name: str, pwd: str, ht: str, dbname: str) -> SQLDatabase:
    uri = f"mysql+pymysql://{name}:{pwd}@{ht}/{dbname}"
    print(f"Attempting to connect to MySQL at: {ht} (db: {dbname})...")
    try:
        db = SQLDatabase.from_uri(uri, sample_rows_in_table_info=3)
        print(f"✅ Successfully connected to MySQL at: {ht}")
        return db
    except Exception as e:
        print(f"❌ Failed to connect to MySQL at: {ht}. Error: {e}")
        raise


def load_azure_db() -> SQLDatabase:
    server   = os.getenv("AZURE_SQL_SERVER", "localhost")
    database = os.getenv("AZURE_SQL_DATABASE")
    user     = os.getenv("AZURE_SQL_USER")
    pwd      = os.getenv("AZURE_SQL_PASSWORD")
    driver   = os.getenv("AZURE_SQL_DRIVER", "ODBC Driver 18 for SQL Server")
    driver_enc = urllib.parse.quote_plus(driver)

    uri = (
        f"mssql+pyodbc://{user}:{pwd}"
        f"@{server}:1433/{database}"
        f"?driver={driver_enc}"
        f"&Encrypt=no"  # disable encryption for local SQL Edge
        f"&TrustServerCertificate=yes"
        f"&Connection+Timeout=30"
    )

    print(f"Attempting to connect to Azure SQL Edge at: {server} (db: {database})...")
    try:
        db = SQLDatabase.from_uri(uri, sample_rows_in_table_info=3)
        print(f"✅ Successfully connected to Azure SQL Edge at: {server}")
        return db
    except Exception as e:
        print(f"❌ Failed to connect to Azure SQL Edge. Error: {e}")
        raise


def parse(tables):
    return ast.literal_eval(tables)


def select_relevant_schemas(state: dict, config: dict) -> dict:
    db = config["configurable"]["db"]
    all_tables = list(db.get_usable_table_names())
    # Prioritize employee tables and limit for token constraints
    limited_tables = sorted(
        all_tables,
        key=lambda t: (
            not ("employee" in t.lower() or "emp_" in t.lower()),
            t
        )
    )[:25]

    state['max_attempts'] = state.get('max_attempts', MAX_ATTEMPTS_DEFAULT)
    question = state['question']

    instruction = SystemMessage(content=SELECT_RELEVANT_TABLES_INSTRUCTION.format(table_names=limited_tables))
    prompt = [instruction, HumanMessage(content=question)]
    model = ChatGroq(model="llama-3.1-8b-instant")
    try:
        raw = model.invoke(prompt).content
        tables = ast.literal_eval(raw)
        relevant = [t for t in tables if t in limited_tables]
    except Exception:
        relevant = []
    # Fallback
    if not relevant and "employee" in question.lower():
        if "employee_information" in limited_tables:
            relevant = ["employee_information"]
    if not relevant:
        return {
            "error_message": INVALID_QUESTION_ERROR,
            "tables_info": "No relevant tables",
            'attempts': 0,
            'answer': '',
            'reasoning': '',
            'queries': []
        }
    return {"tables_info": db.get_table_info(relevant), 'attempts': 0, 'answer': '', 'error_message': '', 'reasoning': '', 'queries': []}


def generate_query(state: dict, config: dict) -> dict:
    db = config["configurable"]["db"]
    question = state["question"]
    tables_info = state["tables_info"]
    queries = state.get("queries")
    instructions = (FIX_QUERY_INSTRUCTIONS if queries and not queries[-1].is_valid else GENERATE_QUERY_INSTRUCTIONS)
    instructions = instructions.format(info=tables_info, queries=queries, error_info=(queries[-1].error_info if queries else ''))

    gen = ChatGroq(model="llama-3.1-8b-instant").with_structured_output(GenQueryResponse)
    resp = gen.invoke([SystemMessage(content=instructions), HumanMessage(content=question)])

    chk = ChatGroq(model="llama-3.3-70b-versatile").with_structured_output(GenQueryResponse)
    corrected = chk.invoke([SystemMessage(content=QUERY_CHECK_INSTRUCTION), AIMessage(content=f"SQLite query: {resp.statement}\nReasoning:{resp.reasoning}")])
    stmt = corrected.statement
    reasoning = resp.reasoning if resp.statement == stmt else f"First: {resp.reasoning}\nCorrection: {corrected.reasoning}"
    query = Query(statement=stmt, reasoning=reasoning)
    return {**state, "queries": [query], "attempts": state.get("attempts",0) + 1}


def execute_query(state: dict, config: dict) -> dict:
    db = config["configurable"]["db"]
    attempts = state.get("attempts",0)
    max_attempts = state.get("max_attempts",0)
    query = state["queries"][-1]
    if attempts > max_attempts:
        return {**state, "error_message": REACH_OUT_MAX_ATTEMPTS_ERROR}
    try:
        res = db.run(query.statement)
        query.result = res if res else []
    except Exception as e:
        query.result = f"ERROR:{e}"
        query.is_valid = False
    return {**state, "attempts": attempts+1, "queries": state["queries"]}


def generate_answer(state: dict, config: dict) -> dict:
    # Directly format result for list queries to avoid token blow-up
    query = state["queries"][-1]
    question = state.get("question","").lower()
    result = query.result
    if isinstance(result, list) and question.startswith("list"):
        # assume list of tuples
        names = [" ".join(map(str,row)) for row in result]
        answer = f"Here are the employee names: {', '.join(names)}"
        return {**state, "answer": answer}
    # Fallback: concise framing
    info = f"SQL query:\n{query.statement}\nResult sample:\n{result[:10] if isinstance(result,list) else result}"
    prompt = [SystemMessage(content=GENERATE_ANSWER_INSTRUCTION.format(query_info=info)), HumanMessage(content=state["question"])]
    resp = ChatGroq(model="llama-3.1-8b-instant").invoke(prompt)
    return {**state, "answer": resp.content}


def general_chat(state: dict, config: dict) -> dict:
    stmt = state['question']
    hist = state.get("general_message",[])
    pack = GeneralMessage(human=stmt, llm="")
    instr = NORMAL_INSTRUCTION.format(history=hist)
    chain = ChatPromptTemplate.from_messages([("system",instr),("placeholder","{messages}")]) | ChatGroq(model="llama-3.1-8b-instant")
    out = chain.invoke({"messages":[stmt]})
    pack.llm = out.content
    return {"answer":out.content, "general_message":hist+[pack]}

# Simple rule detection
def is_related(state: dict) -> bool:
    return bool(re.search(r"\b(select|count|list|how many|show)\b", state.get('question','').lower()))

# Routing
def check_question(state: dict) -> Literal["generate_query","generate_answer","general_chat"]:
    if state.get("error_message")==INVALID_QUESTION_ERROR:
        return "general_chat"
    qs=state.get("queries",[])
    if qs:
        last=qs[-1]
        return "generate_answer" if not str(last.result).startswith("ERROR") else "generate_query"
    return "generate_query" if is_related(state) else "general_chat"

def router(state: dict)->Literal["generate_query","generate_answer"]:
    last=state["queries"][-1]
    return "generate_query" if isinstance(last.result,str) and last.result.startswith("ERROR") else "generate_answer"
