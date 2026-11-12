"""A phase progress post whose response was lost (and whose follow-up lookup also failed) is retried on a later
sync. By then more tasks are done, so a freshly built message has different counts: the retry must reuse the
first attempt's exact text, or the lookup can't find the post that is already in the channel and the customer
gets the same phase announced twice (stress: test_tracking_scale [0.2])."""

from test_tracking import mark, tracker
from test_workflow import GROWTH_CALL, run

from app.tools.registry import AmbiguousToolError, TransientToolError


def test_retried_phase_post_reuses_its_text_and_never_posts_twice(settings):
    h, rid = run(settings, calls=[GROWTH_CALL])
    t = tracker(h)
    t.sync(rid)
    slack = h.world.slack
    real_post, real_find = slack.post, slack.find_post
    faults = {"post": 1, "find": 2}

    def post(channel_id, text):
        real_post(channel_id, text)
        if faults["post"]:
            faults["post"] -= 1
            raise AmbiguousToolError("slack transport: read timed out after send")

    def find_post(channel_id, text):
        if faults["find"]:
            faults["find"] -= 1
            raise TransientToolError("slack: 503 on conversations.history")
        return real_find(channel_id, text)

    slack.post, slack.find_post = post, find_post
    mark(h, phase="Kickoff")
    t.sync(rid)                                   # posted, response lost, lookup failed: outcome unknown
    mark(h, "Data export")                        # more progress before the retry: the counts change
    t.sync(rid)
    t.sync(rid)
    channel = h.store.get_run(rid)["slack_channel_id"]
    kickoff = [m for m in slack.channels[channel]["messages"] if "*Kickoff* phase" in m]
    assert len(kickoff) == 1, kickoff
