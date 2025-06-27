# nodes.py

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

#modified function for azure sql edge db on localhost

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
        f"&Encrypt=no"  # 🔴 disable encryption for local SQL Edge
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
    state['max_attempts'] = state.get('max_attempts', MAX_ATTEMPTS_DEFAULT)
    table_names = db.get_usable_table_names()
    question = state['question']

    toolkit = SQLDatabaseToolkit(db=db, llm=ChatGroq(model="llama-3.1-8b-instant"))
    tools = toolkit.get_tools()
    get_schema_tool = next(tool for tool in tools if tool.name == "sql_db_schema")
    tables_with_schema = {i: get_schema_tool.invoke(i) for i in table_names}

    instruction = SystemMessage(
        content=SELECT_RELEVANT_TABLES_INSTRUCTION.format(table_names=table_names)
    )
    prompt = [instruction, HumanMessage(content=question)]
    model = ChatGroq(model="llama-3.1-8b-instant")
    relevant_tables = model.invoke(prompt)
    relevant_tables = parse(relevant_tables.content)

    if not relevant_tables:
        return {
            "error_message": INVALID_QUESTION_ERROR,
            "tables_info": "No relevant tables",
            'attempts': 0,
            'answer': '',
            'reasoning': '',
            'queries': []
        }

    tables_info = db.get_table_info(relevant_tables)
    return {
        "tables_info": tables_info,
        'attempts': 0,
        'answer': '',
        'error_message': '',
        'reasoning': '',
        'queries': []
    }

def generate_query(state: dict, config: dict) -> dict:
    db = config["configurable"]["db"]
    question = state["question"]
    tables_info = state["tables_info"]
    queries = state.get("queries")

    if queries and not queries[-1].is_valid:
        instructions = FIX_QUERY_INSTRUCTIONS.format(
            info=tables_info,
            error_info=queries[-1].error_info
        )
    else:
        instructions = GENERATE_QUERY_INSTRUCTIONS.format(
            info=tables_info,
            queries=queries
        )

    generator_prompt = [
        SystemMessage(content=instructions),
        HumanMessage(content=question)
    ]
    generator_model = ChatGroq(model="llama-3.1-8b-instant").with_structured_output(GenQueryResponse)
    generator_response = generator_model.invoke(generator_prompt)

    checker_prompt = [
        SystemMessage(content=QUERY_CHECK_INSTRUCTION),
        AIMessage(content=f"SQLite query: {generator_response.statement}\nReasoning:{generator_response.reasoning}")
    ]
    checker_model = ChatGroq(model="llama-3.3-70b-versatile").with_structured_output(GenQueryResponse)
    checker_response = checker_model.invoke(checker_prompt)

    corrected = generator_response.statement != checker_response.statement
    final_reasoning = (
        f"First: {generator_response.reasoning}\n"
        f"Correction: {checker_response.reasoning}"
        if corrected else generator_response.reasoning
    )
    query = Query(statement=checker_response.statement, reasoning=final_reasoning)
    return {
        **state,
        "queries": [query],
        "attempts": state["attempts"] + 1
    }

def execute_query(state: dict, config: dict) -> dict:
    db = config["configurable"]["db"]
    attempts = state["attempts"]
    max_attempts = state["max_attempts"]
    query = state["queries"][-1]

    if attempts > max_attempts:
        return {**state, "error_message": REACH_OUT_MAX_ATTEMPTS_ERROR}

    try:
        result = db.run(query.statement)
        query.result = result if result else "No result found"
    except Exception as e:
        query.result = "ERROR:" + str(e)
        query.is_valid = False

    return {
        **state,
        "attempts": attempts + 1,
        "queries": state["queries"]
    }

def generate_answer(state: dict, config: dict) -> dict:
    if (err := state.get("error_message")):
        return {"error_message": err}

    query = state["queries"][-1]
    ins = GENERATE_ANSWER_INSTRUCTION.format(query_info=query.info)
    prompt = [
        SystemMessage(content=ins),
        HumanMessage(content=state["question"])
    ]
    response = ChatGroq(model="llama-3.1-8b-instant").invoke(prompt)
    return {**state, "answer": response.content}

def general_chat(state: dict, config: dict) -> dict:
    statement = state['question']
    history = state.get("general_message", [])
    pack = GeneralMessage(human=statement, llm="")
    chat_instruction = NORMAL_INSTRUCTION.format(history=history)
    general_prompt = ChatPromptTemplate.from_messages([
        ("system", chat_instruction),
        ("placeholder", "{messages}")
    ])
    llm_chain = general_prompt | ChatGroq(model="llama-3.1-8b-instant")
    response = llm_chain.invoke({"messages": [statement]})
    pack.llm = response.content
    return {
        "answer": response.content,
        "general_message": history + [pack]
    }

def is_related(state: dict) -> bool:
    stmt = state['question']
    tables_info = state.get("tables_info", "")
    instruction = SystemMessage(
        content=CATEGORY_DECIDING_PROMPT.format(
            statement=stmt,
            tables_info=tables_info
        )
    )
    response = ChatGroq(model="llama-3.1-8b-instant").invoke([instruction])
    return "sql" in response.content.lower()

def check_question(state: dict) -> Literal["generate_query", "generate_answer", "general_chat"]:
    if is_related(state):
        return "general_chat" if state.get("error_message") == INVALID_QUESTION_ERROR else "generate_query"
    return "general_chat"

def router(state: dict) -> Literal["generate_query", "generate_answer"]:
    q = state["queries"][-1]
    return "generate_query" if q.result.startswith("ERROR") else "generate_answer"
