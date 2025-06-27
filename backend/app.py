# app.py

import os
import re
import uvicorn
from dotenv import load_dotenv
from typing import Union
from pydantic import BaseModel
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from nodes import load_db, load_azure_db
from graph import graph

# 1. Load all .env variables
load_dotenv()

app = FastAPI()

class CustomCORSMiddleware(CORSMiddleware):
    def is_allowed_origin(self, origin: str) -> bool:
        return bool(re.match(r"^http:\/\/[\w\-]+\.employez\.ai:3000$", origin))

app.add_middleware(
    CustomCORSMiddleware,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QuestionRequest(BaseModel):
    text: str
    clientId: str

@app.post("/ask")
async def ask(body: QuestionRequest):
    print("Received Question:", body.text)
    print("From Client DB:", body.clientId)

    # 2. Choose Azure vs MySQL based on USE_AZURE flag in .env
    use_azure = os.getenv("USE_AZURE", "true").lower() in ("1", "true", "yes")

    if use_azure:
        print("→ Loading Azure SQL Database…")
        db = load_azure_db()
    else:
        print("→ Loading local MySQL Database…")
        db = load_db(
            name=os.getenv("MYSQL_USER"),
            pwd=os.getenv("MYSQL_PASSWORD"),
            ht=os.getenv("MYSQL_HOST"),
            dbname=body.clientId
        )

    # 3. Run your graph with the selected DB
    result = graph.invoke(
        {
            "question": body.text,
            "max_attempts": 2,
        },
        config={"configurable": {"db": db, "thread_id": "1"}}
    )

    # 4. Return answer or error
    if "answer" in result:
        return {"answer": result["answer"]}
    else:
        return {"answer": result.get("error_message", "Unknown error")}

def main(argv=None):
    try:
        uvicorn.run("app:app", host="0.0.0.0", port=8181)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
