<!-- ABOUTME: Judge instructions and the R1-R8 rubric for the decide skill evals. -->
<!-- ABOUTME: run.sh feeds this file, the scenario reference and the transcript to a judge. -->
You are grading how well an AI agent helped someone decide. You get the scenario's
reference (axes, option families, MUSTs, expected shape) and the full transcript:
the agent's replies and the person's answers. Grade only what the transcript shows.
An axis or option counts if the agent named it in substance, in any words. An axis
the person raised on their own does not count for the agent.

Score each criterion:

- R1 frame first: the agent stated what is being decided and whose interests it
  serves (the principal) before proposing options. pass/fail.
- R2 axis recall: how many of the reference's often-missed axes the agent named
  BEFORE its final recommendation, out of the total. Give the count and list them.
- R3 stakeholders: other parties' interests are considered, and every guessed
  interest is marked as an assumption (not stated as fact). pass/fail; n/a when no
  other party exists.
- R4 catastrophes: hard constraints (MUSTs) are explicit, and at least one
  "how could this go badly" item appears. pass/fail.
- R5 option spread: how many distinct reference option families the final output
  covers, plus any genuinely new valid families. Give both counts and list them.
- R6 Pareto: no option in the final set is dominated by another, and each surviving
  option's weak axis (what you give up) is named. pass/fail.
- R7 shape: the output shape matches the reference's "Expected shape". In
  particular, when another party has a say, the output is a menu, not a single
  answer. pass/fail.
- R8 questions: the agent asked at most 2 questions in total that the person had to
  answer, and each one could plausibly change which options win. pass/fail.
- LATE (S3 only): after the late constraint, the agent updated its options without
  redoing the framing from scratch, and the constraint is reflected. pass/fail/n/a.

Reply with ONLY a JSON object, no prose around it:
{"R1":"pass|fail","R2":{"count":N,"total":N,"named":["..."]},"R3":"pass|fail|n/a",
 "R4":"pass|fail","R5":{"reference":N,"novel":N,"families":["..."]},"R6":"pass|fail",
 "R7":"pass|fail","R8":"pass|fail","LATE":"pass|fail|n/a","notes":"one or two sentences"}
