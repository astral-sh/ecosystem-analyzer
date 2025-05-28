def read_from_json_and_plot(filename: str) -> None:
    import json

    import matplotlib.pyplot as plt

    # Read from JSON file
    with open(filename) as json_file:
        data = json.load(json_file)

    statistics = data["statistics"]

    messages = [stat["commit_message"] for stat in statistics][::-1]
    counts = [stat["total_diagnostics"] for stat in statistics][::-1]

    # Remove [ty] prefix from commit messages
    messages = [msg.replace("[ty] ", "") for msg in messages]

    # Limit messages length for better display
    max_length = 70
    messages = [
        msg[:max_length] + "..." if len(msg) > max_length else msg for msg in messages
    ]

    plt.figure(figsize=(16, 10))

    plt.plot(counts, messages, color="#261230")

    plt.grid(axis="y", linestyle="--", alpha=0.7)
    plt.title("Total number of diagnostics on ecosystem projects")
    plt.xlabel("Number of diagnostics")
    plt.ylabel("ty commit")

    # Adjust layout to prevent message cutoff
    plt.tight_layout()
    plt.savefig("diagnostics_per_commit.png", dpi=300)


if __name__ == "__main__":
    read_from_json_and_plot("history-statistics.json")
