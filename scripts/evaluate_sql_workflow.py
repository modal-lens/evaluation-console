"""Run from any directory: python scripts/evaluate_sql_workflow.py --help."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.sql_evaluation.benchmark import main

if __name__ == "__main__":
    main()
