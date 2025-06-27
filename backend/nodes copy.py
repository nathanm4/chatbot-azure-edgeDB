# nodes.py
from langchain_community.utilities import SQLDatabase
from set_api_keys import *
from prompts import *
from states import *
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_core.prompts import ChatPromptTemplate
from typing import Literal
from langchain_openai import ChatOpenAI
import os
import ast


def set_variables():
    set_env("GROQ_API_KEY")
    set_env("LANGCHAIN_API_KEY")
    set_env("OPENAI_API_KEY")
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = "sql-llm-agent-tracker"


set_variables()


def load_db(name: str, pwd: str, ht: str, dbname: str) -> SQLDatabase:
    connection = SQLDatabase.from_uri(
        f"mysql+pymysql://{name}:{pwd}@{ht}/{dbname}",
        sample_rows_in_table_info=3
    )
    print("Connected to DB:", ht)
    return connection


def parse(tables):
    result = ast.literal_eval(tables)
    return result


def select_relevant_schemas(state: dict, config: dict) -> dict:
    db = config["configurable"]["db"]

    max_attempts = state.get('max_attempts', 0)
    state['max_attempts'] = max_attempts if max_attempts > 0 else MAX_ATTEMPTS_DEFAULT

    table_names = db.get_usable_table_names()
    question = state['question']
    toolkit = SQLDatabaseToolkit(db=db, llm=ChatOpenAI(model="gpt-4o-mini-2024-07-18"))
    tools = toolkit.get_tools()
    get_schema_tool = next(tool for tool in tools if tool.name == "sql_db_schema")
    tables_with_schema = {i: get_schema_tool.invoke(i) for i in table_names}

    instruction = SystemMessage(content=SELECT_RELEVANT_TABLES_INSTRUCTION.format(table_names=table_names))
    prompt = [instruction, HumanMessage(content=question)]
    model = ChatOpenAI(model="gpt-4o-mini-2024-07-18")
    relevant_tables = model.invoke(prompt)
    relevant_tables = parse(relevant_tables.content)

    if not relevant_tables:
        return {"error_message": INVALID_QUESTION_ERROR, "tables_info": "No relevant tables", 'attempts': 0, 'answer': '', 'reasoning': '', 'queries': []}

    tables_info = db.get_table_info(relevant_tables)
    return {"tables_info": tables_info, 'attempts': 0, 'answer': '', 'error_message': '', 'reasoning': '', 'queries': []}


def generate_query(state: dict, config: dict) -> dict:
    db = config["configurable"]["db"]

    question = state["question"]
    tables_info = state["tables_info"]
    queries = state.get("queries")

    if queries:
        if queries[-1].is_valid:
            instructions = GENERATE_QUERY_INSTRUCTIONS.format(info=tables_info, queries=queries)
        else:
            instructions = FIX_QUERY_INSTRUCTIONS.format(info=tables_info, error_info=queries[-1].error_info)
    else:
        instructions = GENERATE_QUERY_INSTRUCTIONS.format(info=tables_info, queries=queries)

    generator_prompt = [SystemMessage(content=instructions), HumanMessage(content=question)]
    generator_model = ChatOpenAI(model="gpt-4o-mini-2024-07-18").with_structured_output(GenQueryResponse)
    generator_response = generator_model.invoke(generator_prompt)

    checker_prompt = [
        SystemMessage(content=QUERY_CHECK_INSTRUCTION),
        AIMessage(content=f"SQLite query: {generator_response.statement}\n Reasoning:{generator_response.reasoning}")
    ]
    checker_model = ChatGroq(model="llama-3.3-70b-versatile").with_structured_output(GenQueryResponse)
    checker_response = checker_model.invoke(checker_prompt)

    corrected = generator_response.statement != checker_response.statement
    final_reasoning = (
        f"First: {generator_response.reasoning}\nCorrection: {checker_response.reasoning}"
        if corrected else generator_response.reasoning
    )
    query = Query(statement=checker_response.statement, reasoning=final_reasoning)
    return {**state, "queries": [query], "attempts": state["attempts"] + 1}


def execute_query(state: dict, config: dict) -> dict:
    db = config["configurable"]["db"]

    attempts = state["attempts"]
    max_attempts = state["max_attempts"]
    query = state["queries"][-1]

    if attempts > max_attempts:
        return {**state, "error_message": REACH_OUT_MAX_ATTEMPTS_ERROR}

    try:
        query_result = db.run(query.statement)
        query.result = query_result if query_result else "No result found"
    except Exception as e:
        query.result = "ERROR:" + str(e)
        query.is_valid = False

    return {**state, "attempts": attempts + 1, "queries": state["queries"]}


def generate_answer(state: dict, config: dict) -> dict:
    if error_message := state.get("error_message"):
        return {"error_message": error_message}

    query = state["queries"][-1]
    generate_answer_ins = GENERATE_ANSWER_INSTRUCTION.format(query_info=query.info)
    prompt = [SystemMessage(content=generate_answer_ins), HumanMessage(content=state["question"])]
    response = ChatOpenAI(model="gpt-4o-mini-2024-07-18").invoke(prompt)
    return {**state, "answer": response.content}


def general_chat(state: dict, config: dict) -> dict:
    statement = state['question']
    message_history = state.get("general_message", [])
    pack = GeneralMessage(human=statement, llm="")
    chat_instruction = NORMAL_INSTRUCTION.format(history=message_history)
    general_prompt = ChatPromptTemplate.from_messages([("system", chat_instruction), ("placeholder", "{messages}")])
    general_task_llm = general_prompt | ChatGroq(model="llama-3.1-8b-instant")
    response = general_task_llm.invoke({"messages": [statement]})
    pack.llm = response.content
    return {"answer": response.content, "general_message": message_history + [pack]}


def is_related(state: dict) -> bool:
    statement = state['question']
    tables_info = state.get("tables_info", "")
    category_deciding_instruction = SystemMessage(content=CATEGORY_DECIDING_PROMPT.format(statement=statement, tables_info=tables_info))
    category_deciding_llm = ChatOpenAI(model="gpt-4o-mini-2024-07-18")
    response = category_deciding_llm.invoke([category_deciding_instruction])
    return "sql" in response.content.lower()


def check_question(state: dict) -> Literal["generate_query", "generate_answer", "general_chat"]:
    if is_related(state):
        if state.get("error_message") == INVALID_QUESTION_ERROR:
            return "general_chat"
        return "generate_query"
    else:
        return "general_chat"


def router(state: dict) -> Literal["generate_query", "generate_answer"]:
    query = state["queries"][-1]
    if query.result.startswith("ERROR"):
        return "generate_query"
    else:
        return "generate_answer"
