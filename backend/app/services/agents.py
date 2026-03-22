"""Agent registry for content chat."""

from dataclasses import dataclass


@dataclass
class Agent:
    id: str
    name: str
    system_prompt_template: str
    model: str = "claude-sonnet-4-6"


AGENTS: list[Agent] = [
    Agent(
        id="default",
        name="Content Q&A",
        system_prompt_template="""\
You are a helpful assistant that answers questions about a specific piece of content. \
Be concise and direct.

When referencing specific moments in a video or podcast, cite the timestamp in [MM:SS] \
or [HH:MM:SS] format. These citations become clickable links for the user, so use them \
whenever you mention something that happened at a specific time.

Content details:
Title: {title}
Type: {content_type}

{summary_block}\
{transcript_block}\
{content_block}""",
    ),
    Agent(
        id="five-bullets",
        name="Five Bullet Pointer",
        system_prompt_template="""\
You are an assistant that always answers questions with exactly five bullet points of \
key insights. Each bullet point must include a timestamp citation in [MM:SS] or [HH:MM:SS] \
format indicating where in the content that insight was found. These citations become \
clickable links for the user.

Format every response as:
- **Short Heading** - explanation of the insight [MM:SS]
- **Short Heading** - explanation of the insight [MM:SS]
- **Short Heading** - explanation of the insight [MM:SS]
- **Short Heading** - explanation of the insight [MM:SS]
- **Short Heading** - explanation of the insight [MM:SS]

Each bullet must start with a bold 2-4 word heading that summarizes the point, followed \
by a dash and the detailed explanation. Always provide exactly five bullets, no more, no \
less. Each bullet should be a distinct, substantive insight relevant to the user's question.

Content details:
Title: {title}
Type: {content_type}

{summary_block}\
{transcript_block}\
{content_block}""",
    ),
]

_AGENT_MAP = {a.id: a for a in AGENTS}


def get_agents() -> list[Agent]:
    return AGENTS


def get_agent(agent_id: str) -> Agent | None:
    return _AGENT_MAP.get(agent_id)


def build_system_prompt(agent: Agent, *, title: str, content_type: str,
                        summary: str = "", transcript: str = "",
                        content_markdown: str = "") -> str:
    summary_block = f"Summary:\n{summary}\n\n" if summary else ""
    transcript_block = f"Transcript:\n{transcript[:30000]}\n\n" if transcript else ""
    content_block = f"Article content:\n{content_markdown[:30000]}\n" if content_markdown else ""

    return agent.system_prompt_template.format(
        title=title,
        content_type=content_type,
        summary_block=summary_block,
        transcript_block=transcript_block,
        content_block=content_block,
    )
