# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "numpy",
#     "plotly",
# ]
# ///


def read_from_json_and_plot(filename: str) -> None:
    import json

    import plotly.graph_objects as go

    # Read from JSON file
    with open(filename) as json_file:
        data = json.load(json_file)

    statistics = data["statistics"]

    messages = [stat["commit_message"] for stat in statistics]
    counts = [stat["total_diagnostics"] for stat in statistics]

    # Remove [ty] prefix from commit messages
    clean_messages = [msg.replace("[ty] ", "") for msg in messages]

    # Create commit indices for x-axis
    commit_indices = list(range(len(counts)))

    # Create the main trace with hover info
    main_trace = go.Scatter(
        x=commit_indices,
        y=counts,
        mode="lines+markers",
        line={"color": "#261230", "width": 3},
        marker={"size": 6, "color": "#261230"},
        # fill="tonexty",
        # fillcolor="rgba(38, 18, 48, 0.3)",
        name="Diagnostics",
        hovertemplate="<b>%{customdata}</b><br>"
        + "Diagnostics: %{y}<br>"
        + "<extra></extra>",
        customdata=clean_messages,
    )

    # Create the figure
    fig = go.Figure()

    # Add traces
    fig.add_trace(main_trace)

    # Update layout
    fig.update_layout(
        # title={
        #     "text": "ty diagnostics per commit",
        #     "x": 0.5,
        #     "font": {"size": 20, "family": "Arial Black"},
        # },
        xaxis_title="Commit history →",
        yaxis_title="Total Diagnostics",
        width=700,
        height=400,
        margin={"l": 2, "r": 2, "b": 2, "t": 2, "pad": 4},
        hovermode="x unified",
        showlegend=False,
        template="plotly_white",
        font={"size": 12},
        xaxis={
            "showgrid": False,
            "gridwidth": 1,
            "gridcolor": "rgba(0,0,0,0.1)",
            "tickmode": "linear",
            # dtick=1 if len(commit_indices) <= 50 else max(1, len(commit_indices) // 20),
        },
        yaxis={
            "showgrid": True,
            "gridwidth": 1,
            "gridcolor": "rgba(0,0,0,0.1)",
            "range": [min(counts) * 0.98, max(counts) * 1.02],
        },
    )

    # Save as interactive HTML
    fig.write_html("diagnostics_per_commit.html")

    # Also save as static PNG for backwards compatibility
    fig.write_image("diagnostics_per_commit.png", width=1400, height=700)


if __name__ == "__main__":
    read_from_json_and_plot("history-statistics.json")
