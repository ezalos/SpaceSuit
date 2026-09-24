# ABOUTME: Tests for the live-thread walk and the research classifier.
# ABOUTME: The stop_reason cases matter most: research is asynchronous and stop_reason lies.
from conftest import (
    artifact_block, assistant, conversation, human, research_done, research_started,
    text_block, tool_result, tool_use,
)

from deep_research_web.thread import (
    ThreadState, assistant_text, classify, live_thread, newest_artifact, research_task_id,
)


def test_live_thread_follows_the_leaf_and_ignores_abandoned_branches():
    conv = conversation([
        human("q", "h1"),
        assistant([text_block("old branch")], "a-old", "h1", stop_reason="end_turn"),
        assistant([text_block("live branch")], "a-live", "h1", stop_reason="end_turn"),
    ], leaf="a-live")
    assert [m["uuid"] for m in live_thread(conv)] == ["h1", "a-live"]


def test_running_even_though_the_turn_ended():
    c = classify(research_started(stop_reason="end_turn"))
    assert c.state is ThreadState.RUNNING and c.artifact is None


def test_done_when_the_artifact_lands():
    c = classify(research_done("# R\n\nbody"))
    assert c.state is ThreadState.DONE
    assert c.artifact["content"] == "# R\n\nbody"


def test_failed_when_the_turn_ended_without_research():
    conv = conversation([human("q", "h1"), assistant([text_block("Here is a quick answer.")], "a1", "h1", stop_reason="end_turn")], leaf="a1")
    c = classify(conv)
    assert c.state is ThreadState.NEEDS_REPLY and c.text == "Here is a quick answer."


def test_empty_when_nothing_has_been_said_yet():
    assert classify(conversation([human("q", "h1")], leaf="h1")).state is ThreadState.EMPTY


def test_newest_artifact_wins_over_an_earlier_one():
    conv = conversation([
        human("q", "h1"),
        assistant([artifact_block("first")], "a1", "h1", stop_reason="end_turn"),
        human("more", "h2", parent="a1"),
        assistant([artifact_block("second")], "a2", "h2", stop_reason="end_turn"),
    ], leaf="a2")
    assert newest_artifact(live_thread(conv))["content"] == "second"


def test_artifact_on_an_abandoned_branch_does_not_count():
    conv = conversation([
        human("q", "h1"),
        assistant([artifact_block("abandoned")], "a-old", "h1", stop_reason="end_turn"),
        assistant([tool_use("launch_extended_search_task", {}, id="t")], "a-live", "h1", stop_reason="end_turn"),
    ], leaf="a-live")
    assert classify(conv).state is ThreadState.RUNNING


def test_task_id_is_read_from_the_tool_result():
    assert research_task_id(live_thread(research_started())) == "wf-123"
    conv = conversation([human("q", "h1"), assistant([tool_result("launch_extended_search_task", "task wf-9f started")], "a1", "h1")], leaf="a1")
    assert research_task_id(live_thread(conv)) == "wf-9f"


def test_no_leaf_pointer_falls_back_to_index_order():
    conv = conversation([assistant([text_block("b")], "a1", "h1"), human("a", "h1")], leaf=None)
    conv["chat_messages"][0]["index"] = 1
    assert [m["uuid"] for m in live_thread(conv)] == ["h1", "a1"]


def test_classify_calls_an_answer_without_research_needs_reply():
    # The assistant spoke and never launched research: a clarifying question, a refusal and a
    # plain answer are indistinguishable here, and all three want the same thing from Louis.
    conv = conversation([
        human("q", "h1"),
        assistant([text_block("Which Astra do you mean?")], "a1", "h1", stop_reason="end_turn"),
    ], leaf="a1")
    c = classify(conv)
    assert c.state is ThreadState.NEEDS_REPLY
    assert c.text == "Which Astra do you mean?"


def test_needs_reply_reason_tells_louis_to_answer():
    from deep_research_web.thread import failure_reason

    assert "answer" in failure_reason(ThreadState.NEEDS_REPLY)
