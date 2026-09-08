# Hermes ReAct Pattern Reference

## What is ReAct?

ReAct (Reason + Act) is a multi-step reasoning pattern where the LLM:
1. **Reasons** about what information it needs
2. **Acts** by calling a tool (OCR in our case)
3. **Observes** the tool result
4. Repeats until it has enough information to answer

## How it works in Hermes

User goal: "Check if the invoice total matches the sum of line items" ↓ Iteration 1: Thought: I need to see the full invoice structure Action: ocr_image(mode=document) Obs: # Invoice\n## Line Items\nItem A: $100\nItem B: $200\nTotal: $300

Iteration 2: Thought: I can see Total=$300. Let me verify line items sum Action: ocr_image(mode=find, find_term="Total") Obs: Grand Total: $300.00

Final Answer: The invoice total ($300) matches the sum of line items ($100 + $200 = $300). The invoice is mathematically correct.


## Max iterations: 6

Hermes stops after 6 tool calls to prevent infinite loops.
If 6 iterations are not enough, break the task into smaller goals.

## When to prefer ReAct over single-pass

- Cross-referencing multiple fields ("does X match Y?")
- Conditional extraction ("if this is an invoice, extract these fields")
- Verification tasks ("is this document complete?")
- Tasks requiring multiple OCR modes on the same document
