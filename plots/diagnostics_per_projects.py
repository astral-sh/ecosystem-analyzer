def read_from_json_and_plot(filename: str) -> None:
    import json

    import matplotlib.pyplot as plt

    # Read from JSON file
    with open(filename) as json_file:
        data = json.load(json_file)

    outputs = data["outputs"]

    projects = [output["project"] for output in outputs]
    counts = [len(output["diagnostics"]) for output in outputs]

    # sort projects and counts by counts
    sorted_indices = sorted(range(len(counts)), key=lambda i: counts[i], reverse=True)
    projects = [projects[i] for i in sorted_indices]
    counts = [counts[i] for i in sorted_indices]

    # Get the top 15 and bottom 15 projects
    top_projects = projects[:15]
    top_counts = counts[:15]
    bottom_projects = projects[-15:]
    bottom_counts = counts[-15:]

    # Create a figure with two vertically stacked subplots
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 12), sharex=False)

    # Top 15 projects bar chart
    ax1.barh(top_projects[::-1], top_counts[::-1], color="#6340AC")
    ax1.set_title("Projects with most diagnostics")
    ax1.set_xlabel("Number of Diagnostics")

    # Bottom 15 projects bar chart
    ax2.barh(bottom_projects[::-1], bottom_counts[::-1], color="#6F5D6F")
    ax2.set_title("Projects with least diagnostics")
    ax2.set_xlabel("Number of Diagnostics")

    # Adjust layout and save the figure
    plt.tight_layout()
    plt.savefig("diagnostics_per_project.png")


if __name__ == "__main__":
    read_from_json_and_plot("history-diagnostics-9-61e7348.json")
