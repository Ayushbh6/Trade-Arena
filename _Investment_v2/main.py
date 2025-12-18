import sys
from agent.core import run_agent
from dotenv import load_dotenv

load_dotenv()

def main():
    print("Welcome to Investment Agent V2 (CodeAct Edition)")
    print("-" * 50)
    
    if len(sys.argv) > 1:
        user_prompt = " ".join(sys.argv[1:])
    else:
        user_prompt = input("Enter your trading request: ")
    
    run_agent(user_prompt)

if __name__ == "__main__":
    main()
