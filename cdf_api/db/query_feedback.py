import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_all_feedback, get_feedback_stats


def show_all_records():
    rows = get_all_feedback(limit=10)
    print(f"\n===== latest {len(rows)} feedback records =====\n")

    for row in rows:
        action_text = "like" if row["like_type"] == 1 else "dislike"
        print(f"[ID: {row['id']}] {row['timestamp']}")
        print(f"  action: {action_text}")
        print(f"  conversation_id: {row['conversation_id']}")
        print(f"  task_id: {row['target_task_id']}")
        try:
            history = json.loads(row["history_json"])
        except Exception:
            history = []
        print(f"  sampled_history_count: {len(history)}")
        print("-" * 60)

    stats = get_feedback_stats()
    print("\n===== feedback stats =====")
    for like_type, count in stats.items():
        action_text = "like" if like_type == 1 else "dislike"
        print(f"  {action_text}: {count}")


if __name__ == "__main__":
    show_all_records()
