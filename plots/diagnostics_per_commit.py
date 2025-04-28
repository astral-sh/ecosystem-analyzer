def read_from_json_and_plot(filename: str) -> None:
    import json

    import matplotlib.pyplot as plt

    # Read from JSON file
    with open(filename) as json_file:
        data = json.load(json_file)

    statistics = data["statistics"]

    messages = [stat["commit_message"] for stat in statistics]
    counts = [stat["total_diagnostics"] for stat in statistics]

    # Remove [red-knot] prefix from commit messages
    messages = [msg.replace("[red-knot] ", "") for msg in messages]
    # Limit messages
    max_length = 50
    messages = [
        msg[:max_length] + "..." if len(msg) > max_length else msg for msg in messages
    ]

    # Generate a line plot which shows number of diagnostics per commit
    plt.figure(figsize=(10, 10))
    plt.plot(messages, counts, marker="o", color="#261230", lw=2)
    plt.grid(axis="x")
    plt.title("Total number of diagnostics on ecosystem projects")
    plt.ylabel("Number of diagnostics")
    plt.xlabel("Red Knot commit")
    plt.xticks(rotation=45, ha="right")
    # plt.ylim(0, 5000)
    plt.tight_layout()
    plt.savefig("diagnostics_per_commit.png")


if __name__ == "__main__":
    read_from_json_and_plot("history-statistics.json")
