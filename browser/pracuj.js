// Run on an it.pracuj.pl search page via Playwright browser_evaluate.
// Returns Job-shaped objects for `jobhunt ingest`.
() => {
  const data = JSON.parse(document.getElementById('__NEXT_DATA__').textContent);
  const queries = data.props.pageProps.dehydratedState.queries;
  const groups = queries.flatMap(q => (q.state.data && q.state.data.groupedOffers) || []);
  const level = (levels) => {
    const s = (levels || []).join(' ').toLowerCase();
    if (s.includes('praktykant') || s.includes('stażyst') || s.includes('trainee')) return 'intern';
    if (s.includes('junior') || s.includes('asystent')) return 'junior';
    if (s.includes('mid') || s.includes('regular')) return 'mid';
    if (s.includes('senior') || s.includes('ekspert') || s.includes('kierownik')) return 'senior';
    return '';
  };
  return groups.flatMap(g => g.offers.map(o => ({
    source: 'pracuj',
    source_id: String(o.partitionId),
    title: g.jobTitle,
    company: g.companyName,
    url: o.offerAbsoluteUri,
    apply_url: o.offerAbsoluteUri,
    location: (o.displayWorkplace || '').split(',')[0],
    remote: (g.workModes || []).some(m => /zdaln|remote/i.test(m)),
    seniority: level(g.positionLevels),
    description: [g.jobDescription, (g.aiSummary || '').replace(/<[^>]+>/g, ' ')].join('\n'),
    skills: g.technologies || [],
    salary: g.salaryDisplayText || '',
    posted_at: g.lastPublicated || '',
    expires_at: g.expirationDate || '',
    // Pracuj adds a Polish AI summary to every offer; judge the language from the original text.
    languages: /^(Twój zakres|Twoje zadania|Zakres obowiązków)/.test(g.jobDescription || '') ? ['Polish:listing'] : [],
  })));
}
