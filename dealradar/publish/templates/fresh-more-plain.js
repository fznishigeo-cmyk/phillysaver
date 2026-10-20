  function freshCycleText() { return ''; }
  function freshRoughCount(n) {
    if (n >= 1000) return '1,000+';
    return n < 100 ? String(n) : (Math.floor(n / 100) * 100).toLocaleString('en-US') + '+';
  }
  function freshControls() {
    var total = host.state.deals.filter(d => d.status !== 'dead').length;
    return '<div class="more fresh-more"><button type="button" id="fresh-all" class="pwbtn">See all ' + freshRoughCount(total) + (total === 1 ? ' deal' : ' deals') + '</button></div>';
  }
