// Run on a Gmail search-results page via Playwright browser_evaluate.
// Returns one object per visible thread row for `jobhunt history import`.
// Reads only list-view metadata (sender, subject, snippet, date) — never opens messages.
// Only rows of the visible results list: Gmail keeps hidden copies of other views (inbox) in the DOM.
() => [...document.querySelectorAll('div[role="main"] tr.zA')].filter(r => r.offsetParent).map(r => {
  const sender = r.querySelector('.yW span[email]');
  const date = r.querySelector('td.xW span[title], .xW span[title]');
  const subj = r.querySelector('span.bog');
  const snip = r.querySelector('span.y2');
  const thread = r.querySelector('[data-legacy-thread-id]');
  return {
    sender_name: sender ? sender.getAttribute('name') || sender.innerText : '',
    sender_email: sender ? sender.getAttribute('email') : '',
    subject: subj ? subj.innerText.trim() : '',
    snippet: snip ? snip.innerText.replace(/^\s*-\s*/, '').trim() : '',
    date: date ? date.getAttribute('title') : '',
    thread_id: thread ? thread.getAttribute('data-legacy-thread-id') : '',
  };
})
