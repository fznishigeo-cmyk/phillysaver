  var confettiCanvas = null, confettiFrame = 0;
  function stopConfetti() {
    if (confettiFrame) cancelAnimationFrame(confettiFrame);
    confettiFrame = 0;
    if (confettiCanvas) confettiCanvas.remove();
    confettiCanvas = null;
  }
  function startConfetti() {
    if (document.hidden || host.reduceMotion() || !freshView()) return;
    stopConfetti();
    var canvas = document.createElement('canvas'), ctx = canvas.getContext('2d');
    if (!ctx) return;
    confettiCanvas = canvas; canvas.id = 'drop-confetti'; canvas.setAttribute('aria-hidden','true');
    canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:35';
    document.body.appendChild(canvas);
    var colors = ['149,58,255','255,195,41','255,101,26','123,92,255','76,126,255','71,192,255','255,47,39','255,91,134','233,122,208'];
    var particles = [], emitted = 0, start = null, last = null, width = 0, height = 0, ratio = 0;
    function frame(now) {
      confettiFrame = 0;
      if (document.hidden || host.reduceMotion() || !freshView()) { stopConfetti(); return; }
      if (start === null) start = last = now;
      var elapsed = (now-start)/1000, dt = Math.min(.04,(now-last)/1000); last = now;
      if (elapsed >= 10) { stopConfetti(); return; }
      var dpr = Math.min(window.devicePixelRatio || 1, 3);
      if (width !== window.innerWidth || height !== window.innerHeight || ratio !== dpr) {
        width = window.innerWidth; height = window.innerHeight; ratio = dpr;
        canvas.width = Math.round(width*dpr); canvas.height = Math.round(height*dpr);
        ctx.setTransform(dpr,0,0,dpr,0,0);
      }
      var target = elapsed < 1 ? Math.min(300,100+Math.floor(elapsed*200)) : 300;
      while (emitted < target) {
        var back = emitted % 2 === 0;
        particles.push({x:width/2,y:-20,vx:(Math.random()-.5)*Math.max(700,width*1.5),vy:80+Math.random()*230,
          scale:back?.55:1,alpha:back?.5:1,color:colors[emitted%9],circle:emitted%3===0,
          phase:Math.random()*Math.PI*2,spin:2+Math.random()*4});
        emitted++;
      }
      ctx.clearRect(0,0,width,height);
      var drag = Math.exp(-(elapsed < 1 ? .3 : 1.4)*dt), gravity = elapsed < .5 ? 100 : elapsed < 2 ? 380 : 140;
      // Draw the distant layer first; rectangles flutter on three axes.
      for (var layer = 0; layer < 2; layer++) particles.forEach(function (p) {
        if ((p.scale === 1 ? 1 : 0) !== layer) return;
        p.vx *= drag; p.vy = p.vy* Math.exp(-.3*dt) + gravity*dt;
        p.x += (p.vx + Math.sin(elapsed*3+p.phase)*35)*dt; p.y += p.vy*p.scale*dt;
        ctx.save(); ctx.translate(p.x,p.y); ctx.rotate(p.phase+elapsed*p.spin);
        ctx.scale(p.scale*Math.cos(elapsed*p.spin+p.phase),p.scale*(.35+.65*Math.abs(Math.sin(elapsed*2+p.phase))));
        ctx.globalAlpha = p.alpha*Math.min(1,(10-elapsed)/2); ctx.fillStyle = 'rgb('+p.color+')';
        if (p.circle) { ctx.beginPath(); ctx.arc(0,0,6,0,Math.PI*2); ctx.fill(); }
        else ctx.fillRect(-11,-7,22,14);
        ctx.restore();
      });
      particles = particles.filter(p=>p.y<height+30 && p.x>-100 && p.x<width+100);
      if (elapsed >= 1 && !particles.length) { stopConfetti(); return; }
      confettiFrame = requestAnimationFrame(frame);
    }
    confettiFrame = requestAnimationFrame(frame);
  }
  document.addEventListener('visibilitychange', function () { if (document.hidden) stopConfetti(); });
