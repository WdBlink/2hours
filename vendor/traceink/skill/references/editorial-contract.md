# Evidence-led review contract

## Purpose

Prepare a bounded, evidence-linked reconstruction of Agent work so the user can understand it quickly and form their own judgment. Give an evidence-based assessment when asked; do not write the user's reflection or treat that assessment as their commitment.

## Research lineage and source corrections

The Knowledge-Centric Self-Improvement research protocol contributes a Prompt-led editorial discipline, not this product's human closeout lifecycle.

- Its formal per-task Prompt requests exactly six values: one load-bearing assumption, evidence, optional `evidence_post_id`, one concrete next-generation change, one falsifiable predicted outcome, and confidence.
- `evidence_post_id` cites evidence. It is not `parent_post_id` and does not point to a prior human decision.
- The formal cross-task relations are `AGREE`, `DISAGREE`, and `SYNTHESIZE`. `refine` appears in project-page explanatory prose, not as a fourth formal paper operator.
- Its distillation favors actionable, scoped, evidence-grounded claims with applicability boundaries and calibrated confidence.
- The research protocol does not supply human reflection, adoption, migration, sealing, or background-work authority. Those are product interactions added after evidence preparation.

Do not copy the six values into a rigid wire schema. Use them as an editing rubric that can benefit from future model improvements.

## Reader-first expression

Write for a capable reader who has not followed every technical step. Optimize for understanding and judgment, not maximum brevity or a complete execution log.

- **Answer before expanding.** For a specific question, open with the direct answer and its most important limit. Then develop the evidence. A broad day/week review still starts with its workline index; this is not permission to invent a daily verdict.
- **Explain terms where they become useful.** Prefer familiar Chinese, introduce the technical name in parentheses when needed, and distinguish easily confused concepts. A short analogy may help, but explain where it stops applying. Do not assume the reader knows abbreviations just because the artifacts use them.
- **Connect work to purpose and impact.** Explain what problem each change addressed, what was tried, what happened, and what the result means for the reader. Preserve meaningful route changes; omit routine tool chatter and implementation identifiers that do not affect the judgment.
- **Make numbers interpretable.** State what was measured, compared with what, on how many independent cases, and how failures count. Distinguish a completed check from demonstrated usefulness. Mark proposed tables and hypothetical values before showing them. Put any limitation that changes the takeaway beside the result.
- **Let structure earn its space.** Use connected paragraphs for reasoning and a compact table for genuinely parallel methods, metrics, or options. Headings are optional. Do not force every response into six sections, a fixed number of tables, or a fixed length; expand what the reader needs to understand or decide, and stop when that is covered.
- **Keep evidence available without interrupting the explanation.** Put compact evidence markers next to material claims; retain exact paths, Session IDs, and time/message locators in the evidence register. A short answer must not erase disagreement, uncertainty, or missing coverage.
- **Carry the reasoning through.** Explain what the evidence supports and what observation could change that assessment. When asked for a recommendation, give one with its reason and scope, clearly as the assistant's recommendation. Reserve the final human question for a real choice of goals, resources, priorities, or risk; an unanswered technical question is not automatically the user's decision.

Illustrative contrast, not evidence to reuse in later reviews:

```text
Too compressed:
P0 gate PASS; DSC inactive; N=1; generalization unverified. Next step?

Reader-first:
本轮已跑通评测流程，但还不能说新算法提高了定位精度。实际只测了一张照片；把这张照片分别配上两种参考图，仍然只算一个独立样本。[E1]

新算法希望只接受可靠的位置修正。这次它没有接受任何修正，直接返回了原有方法的结果。因此，这个案例验证了回退分支，还没有展示改进收益。[E2]

判断是否值得继续，需要同时看两件事：其他独立照片上能否产生改善，以及产生改善时是否也增加了严重错误。只看“没有变差”还不够，因为一直沿用原结果也能做到。
```

## Editorial recipe

For each workline:

1. Recover the concrete prior assumption or operating context only when evidence supports it. Otherwise say `未读到可靠的先前判断`.
2. Reconstruct the smallest sequence that explains why the current state differs from the start. Preserve meaningful failed paths and route changes.
3. Attach exact basis evidence to every material interpretation.
4. Describe a **possible** change. Avoid adopted/confirmed language until the user explicitly acts.
5. State a future observation that could strengthen, narrow, or overturn the possible change.
6. Preserve counter-evidence, disagreement, task scope, and confidence instead of averaging them into smooth prose.
7. Surface a decision that genuinely requires the user, if one remains after answering the researchable questions.

Prefer concrete wording:

```text
原来的判断
可能是任务调度出了问题：系统没有把任务按合适的顺序送去执行。

发生了什么
三轮调整都没有稳定改善；11 个失败任务中，有 8 个在排队执行前就丢失了必要限制。[E1][E2]

可能产生的变化 · AI 整理，尚未采纳
任务信息可能在提交时就不完整，后续再调整执行顺序也难以补救。

未来如何验证
把这些任务需要的信息补全，并保持执行顺序不变。如果补充信息后明显减少了执行前的修补次数，这个解释就更可信。

仍需你判断
是否把下一轮实验预算优先用于检查任务信息的完整性？
```

Avoid vague wording such as “继续优化”“加强关注”“需要更多研究” unless the evidence cannot support anything more specific.

## Evidence contract

Use stable local labels within one response:

```text
[E1] Codex · session <id> · /absolute/path.jsonl · 09:12–11:22
[E2] Claude Code · session <id> · /absolute/path.jsonl · message 18–24
[E3] Document · /absolute/project/path.md · linked from E2
```

- Put markers beside supported claims, not only in a bibliography.
- Cite only files and ranges actually read.
- Distinguish observed facts from reconstructed inference.
- A generated interpretation without a direct anchor remains visible as `推断：...`.
- Never treat Session metadata titles or thin summaries as evidence authority.
- Never invent a path, Session ID, experiment, result, decision, or artifact.

## Participation contract

Mark only what evidence can support:

- `你参与`: the user asked, constrained, corrected, selected, rejected, or confirmed.
- `Agent 独立推进`: execution proceeded without a user message in that span.
- `共同推进`: interleaved user judgment and Agent execution.
- `无法确定`: timestamps or authorship are insufficient.

Agent activity volume is not human participation and not proof of importance.

## Three evidence meanings

- `basis evidence`: why a possible judgment change deserves consideration.
- `commitment evidence`: an explicit user interaction that adopts or authorizes a judgment. Never infer this from basis evidence.
- `outcome evidence`: later material that tests the prediction attached to an adopted judgment. It never rewrites the earlier record.

During initial review, the skill primarily prepares basis evidence. Commitment appears only after the user explicitly confirms. Outcome belongs to a later review.

## Authority boundary

Generated material may say:

- `证据表明值得重新评估...`
- `可能的变化是...`
- `仍需你判断...`

Generated material must not say:

- `你决定了...`
- `今天我们确认了...`
- `明天必须...`
- `已写入 CTX / 已交给后台 / 已封页`

unless the current conversation contains the exact explicit user action and the corresponding action actually succeeded.

Do not write first-person prose on the user's behalf. Do not infer commitment from confident wording, file modifications, completed tests, or a generated design document.

## Evaluation rubric

Judge output by behavior, not exact headings or JSON equality:

1. **Evidence fidelity:** Can each important claim be reopened and checked?
2. **Grouping quality:** Did shared intent become a workline rather than a list of Session summaries?
3. **Cognitive honesty:** Did the model avoid manufacturing user decisions?
4. **Conflict retention:** Are counter-evidence, uncertainty, and applicability boundaries visible?
5. **Review compression:** Is it materially faster to understand than reading all raw Sessions?
6. **Human question:** If a question is asked, does it expose a real human choice rather than hand back an answerable technical question?

Unknown semantic roles and better future organization are welcome when these six properties remain true.
