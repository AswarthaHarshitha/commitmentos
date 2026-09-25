// @include _common.js
// Calendar Sync / Follow-up Assistant / Completion Detection: turn what the API found into proposals for a person to decide on.

const scanItems = (scan) => (scan && Array.isArray(scan.items) ? scan.items : []);

function proposalEvent(item, executionId, proposedBy = 'SYSTEM') {
  return {
    event: 'proposal.created',
    obligation_id: item.obligation_id,
    action_type: item.action_type,
    title: truncate(item.title, 300),
    rationale: item.rationale ? truncate(item.rationale, 1000) : null,
    payload: item.payload || {},
    proposed_by: proposedBy,
    n8n_execution_id: String(executionId),
  };
}

// completion matches -> the same event shape (a proposal to mark the commitment done; the evidence is the rationale)
function completionProposal(match, executionId) {
  return proposalEvent(
    {
      obligation_id: match.obligation_id,
      action_type: 'COMPLETE_OBLIGATION',
      title: `Mark as done: ${match.obligation_title}`,
      rationale: match.rationale,
      payload: {},
    },
    executionId,
    'AI',
  );
}

function summariseProposals(replies) {
  const created = replies.filter((r) => r && r.created === true).length;
  return { proposals: replies.length, created, already_open: replies.length - created };
}

function assessCompletion(check) {
  return {
    status: check.status,
    candidates: check.candidates || 0,
    llm_called: check.llm_called === true,
    matches: Array.isArray(check.matches) ? check.matches.length : 0,
  };
}

if (typeof module !== 'undefined' && module.exports) module.exports = { scanItems, proposalEvent, completionProposal, summariseProposals, assessCompletion };
