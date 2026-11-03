import sys

from dotenv import load_dotenv

from agent_company.crew import build_crew


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python -m agent_company "<project request>"')
    load_dotenv()
    result = build_crew().kickoff(inputs={"request": " ".join(sys.argv[1:])})
    print(result.raw)


if __name__ == "__main__":
    main()
