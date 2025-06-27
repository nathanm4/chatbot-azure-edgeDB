from pydantic import BaseModel, Field
from typing import List, Any, Dict
import operator
from typing_extensions import TypedDict
from typing import Annotated
from langchain_community.utilities import SQLDatabase

class Query(BaseModel):
    statement: str = Field(description="The SQL query to execute")
    reasoning: str = Field(description="Reasoning behind the query")
    is_valid: bool = Field(True,description="Indicates if the statement is valid")
    result: str = Field("",description="query result")
    error: str = Field("",description="Error message if applicable")
    
    @property
    def info(self) -> str:
        return (f"SQL query:\n{self.statement}\n\n"
    f"The reasoning you used to create that query was:\n{self.reasoning}\n\n"
    f"And this is the result you get:\n{self.result}")
    @property
    def error_info(self) -> str:
        return(f"Wrong SQL query:\n{self.statement}\n\n"
    f"The reasoning you used to create that query was:\n{self.reasoning}\n\n"
    f"And this is the error you got when excuted it: {self.error}")

class GeneralMessage(BaseModel):
    human: str = Field("",description="Human message")
    llm: str = Field("",description="answer by LLM")        

class InputState(TypedDict):
  question: str
  max_attempts: int
  general_message: Annotated[List[GeneralMessage],operator.add]
  db: SQLDatabase  
  

class OutputState(TypedDict):
  answer: str
  error_message: str
  general_message: Annotated[List[GeneralMessage],operator.add]
  
class OverallState(TypedDict):
    question: str
    max_attempts: int
    attempts: int
    answer: str
    error_message: str
    tables_info: str
    reasoning: str
    queries: List[Query]
    db: SQLDatabase  # ✅ Add this to propagate DB instance



class GenQueryResponse(BaseModel):
    statement: str= Field(description="Query statement to be executed")
    reasoning: str= Field(description="Reasoning used to define the query")