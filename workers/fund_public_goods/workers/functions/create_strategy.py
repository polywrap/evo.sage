from typing import List
import uuid
from fund_public_goods.agents.researcher.functions.assign_weights import assign_weights
from fund_public_goods.agents.researcher.functions.evaluate_projects import (
    evaluate_projects,
)
from fund_public_goods.agents.researcher.models.answer import Answer
from fund_public_goods.agents.researcher.models.evaluated_project import (
    EvaluatedProject,
)
from fund_public_goods.agents.researcher.models.project import Project
from fund_public_goods.agents.researcher.models.weighted_project import WeightedProject
import inngest
from fund_public_goods.db.tables.projects import get_projects
from fund_public_goods.db.tables.runs import get_prompt
from fund_public_goods.db.tables.strategy_entries import insert_multiple
from fund_public_goods.workers.events import CreateStrategyEvent
from fund_public_goods.db import client, logs
from supabase import Client


def fetch_projects_data(supabase: Client) -> list[Project]:
    response = get_projects(supabase)
    projects = []

    for item in response.data:
        # project_information = item.model_dump()
        answers = []

        for application in item.get("applications", []):
            for ans in application.get("answers", []):
                answer = {
                    "question": ans.get("question", ""),
                    "answer": ans.get("answer", None),
                }
                answers.append(answer)

        project = Project(
            id=item.get("id", str(uuid.uuid4())),
            title=item.get("title", ""),
            description=item.get("description", ""),
            website=item.get("website", ""),
            answers=answers,
        )
        projects.append(project)

    return projects


@inngest.create_function(
    fn_id="on_create_strategy",
    trigger=CreateStrategyEvent.trigger,
)
async def create_strategy(
    ctx: inngest.Context,
    step: inngest.Step,
) -> str | None:
    data = CreateStrategyEvent.Data.model_validate(ctx.event.data)
    run_id = data.run_id
    supabase = client.create_admin()

    await step.run(
        "extracting_prompt",
        lambda: logs.insert(supabase, run_id, "Extracting prompt from run_id"),
    )

    prompt = await step.run("extract_prompt", lambda: get_prompt(supabase, run_id))

    await step.run(
        "fetching_projects_info",
        lambda: logs.insert(supabase, run_id, "Getting information from data sources"),
    )

    json_projects = await step.run(
        "fetch_projects_data",
        lambda: fetch_projects_data(supabase)
    )
    
    projects: list[Project] = [Project(**json_project) for json_project in json_projects] # type: ignore

    await step.run(
        "assessing",
        lambda: logs.insert(
            supabase,
            run_id,
            "Assessing impact of each project realted to the users interest",
        ),
    )

    json_asessed_projects = await step.run(
        "assess_projects", lambda: evaluate_projects(prompt, projects)
    )
    assessed_projects = [EvaluatedProject(**x) for x in json_asessed_projects]  # type: ignore

    await step.run(
        "determining_funding",
        lambda: logs.insert(
            supabase,
            run_id,
            "Determining the relative funding that the best matching projects need",
        ),
    )

    json_weighted_projects: list[WeightedProject] = await step.run(
        "determine_funding", lambda: assign_weights(assessed_projects)
    )
    weighted_projects = [WeightedProject(**x) for x in json_weighted_projects]  # type: ignore

    await step.run(
        "saving_results_to_db",
        lambda: logs.insert(supabase, run_id, "Generating results"),
    )

    await step.run(
        "save_strategy_to_db", lambda: insert_multiple(run_id, weighted_projects)
    )

    await step.run("result", lambda: logs.insert(supabase, run_id, "STRATEGY_CREATED"))

    return "done"
