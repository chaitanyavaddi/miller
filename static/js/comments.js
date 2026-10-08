/* The comment composer: files picked for a comment are listed until it is sent. */
(function () {
  'use strict';

  function formatSize(bytes) {
    return bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB';
  }

  function pendingFile(file) {
    const row = document.createElement('span');
    row.className = 'file-link file-link--pending';
    row.innerHTML = '<span class="file-link__name"></span><span class="file-link__size"></span>';
    row.querySelector('.file-link__name').textContent = file.name;
    row.querySelector('.file-link__size').textContent = formatSize(file.size);
    return row;
  }

  document.addEventListener('change', e => {
    if (e.target.id !== 'files') return;
    const files = [...e.target.files];
    document.getElementById('pending').replaceChildren(...files.map(pendingFile));
    document.getElementById('composer').classList.toggle('composer--has-files', files.length > 0);
  });
})();
