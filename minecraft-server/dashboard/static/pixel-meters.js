/* Existing telemetry still sets fill.style.width; render it as discrete game pixels. */
(() => {
  const labels = {
    'bar-cpu': ['CPU load', 'CPU LOAD'],
    'bar-ram': ['System memory usage', 'SYSTEM RAM'],
    'bar-disk': ['Disk usage', 'DISK USED'],
    'bar-cpu-temp': ['CPU temperature range', 'TEMP RANGE'],
    'bar-gpu-temp': ['GPU temperature range', 'TEMP RANGE'],
    'upload-progress-bar': ['World upload progress', 'UPLOAD']
  };
  function mount(fill) {
    const wrapper = fill.parentElement;
    if (wrapper.dataset.pixelMeter) return;
    wrapper.dataset.pixelMeter = 'true';
    const [name, caption] = labels[fill.id] || ['Usage', 'USAGE'];
    wrapper.classList.add('pixel-meter');
    wrapper.setAttribute('role', 'progressbar');
    wrapper.setAttribute('aria-label', name);
    wrapper.setAttribute('aria-valuemin', '0');
    wrapper.setAttribute('aria-valuemax', '100');
    fill.setAttribute('aria-hidden', 'true');
    const track = document.createElement('div');
    track.className = 'pixel-meter-track';
    track.setAttribute('aria-hidden', 'true');
    const cells = Array.from({length:16}, () => {
      const cell = document.createElement('span');
      cell.className = 'meter-pixel'; track.append(cell); return cell;
    });
    const meta = document.createElement('div');
    meta.className = 'pixel-meter-meta'; meta.setAttribute('aria-hidden', 'true');
    const title = document.createElement('span'); title.textContent = caption;
    const valueLabel = document.createElement('span');
    meta.append(title, valueLabel);
    wrapper.append(track, meta);
    function update() {
      const raw = Number.parseFloat(fill.style.width);
      const value = Number.isFinite(raw) ? Math.max(0, Math.min(100, raw)) : 0;
      const filled = Math.floor(value * cells.length / 100);
      cells.forEach((cell, index) => {
        cell.classList.toggle('is-filled', index < filled);
        // A character marks the current slot; empty slots remain pellets.
        cell.classList.toggle('is-head', value > 0 && index === Math.min(filled, cells.length-1));
      });
      valueLabel.textContent = `${Number(value.toFixed(1))}%`;
      wrapper.setAttribute('aria-valuenow', String(value));
      wrapper.setAttribute('aria-valuetext', `${Number(value.toFixed(1))} percent`);
      wrapper.dataset.level = value >= 95 ? 'danger' : value >= 85 ? 'warn' : 'normal';
    }
    new MutationObserver(update).observe(fill, {attributes:true,attributeFilter:['style']});
    update();
  }
  function init() { document.querySelectorAll('.progress-bar-wrap > .progress-fill').forEach(mount); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
