
# set_api_keys.py
import os, getpass
from dotenv import load_dotenv

load_dotenv()

def set_env(key: str):
    if key not in os.environ:
        os.environ[key] = getpass.getpass(f"{key}:")




# import getpass
# import os

# from dotenv import load_dotenv

# load_dotenv()

# def set_env(key: str):
#   if key not in os.environ:
#     os.environ[key]=getpass.getpass(f"{key}:")