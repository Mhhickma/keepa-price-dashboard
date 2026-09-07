/* Shared upload controller: stream logical CSV records into bounded, immutable files. */
(() => {
  'use strict';
  const form = document.getElementById('creatorCsvUploadForm');
  if (!form) return;
  const input = document.getElementById('creatorCsvFile');
  const status = document.getElementById('creatorCsvUploadStatus');
  const label = document.getElementById('creatorCsvFileName');
  const endpoint = 'https://script.google.com/macros/s/AKfycbxU4HTktR6zH5Wfbk58V24X-HAE9kZYlzdlm1gqMp1NL_ZGzF7p-0VAL5VeGNfnAyxESA/exec';
  const encoder = new TextEncoder();
  const maxBytes = 2 * 1024 * 1024;
  input.multiple = true;
  input.addEventListener('change', () => { label.textContent = `${input.files.length} CSV file(s) selected`; });

  async function* chunks(file) {
    const reader = file.stream().pipeThrough(new TextDecoderStream('utf-8', {fatal: true})).getReader();
    let header = '', record = '', body = '', size = 0, quoted = false;
    try {
      for (;;) {
        const {value, done} = await reader.read();
        if (done) break;
        for (const ch of value) {
          record += ch;
          if (ch === '"') quoted = !quoted;
          if (record.length > maxBytes) throw new Error('A CSV record exceeds the 2 MB upload limit. Use the documented local import.');
          if (ch !== '\n' || quoted) continue;
          if (!header) { header = record.replace(/^\uFEFF/, ''); size = encoder.encode(header).length; }
          else {
            const bytes = encoder.encode(record).length;
            if (bytes + encoder.encode(header).length > maxBytes) throw new Error('A CSV record is too large for browser upload.');
            if (size + bytes > maxBytes && body) { yield header + body; body = ''; size = encoder.encode(header).length; }
            body += record; size += bytes;
          }
          record = '';
        }
      }
      if (quoted) throw new Error('Unclosed CSV quote. Upload stopped.');
      if (record.trim()) {
        if (!header) throw new Error('CSV needs a header and campaign rows.');
        const bytes = encoder.encode(record + '\n').length;
        if (bytes + encoder.encode(header).length > maxBytes) throw new Error('A CSV record is too large for browser upload.');
        if (size + bytes > maxBytes && body) { yield header + body; body = ''; }
        body += record + '\n';
      }
      if (body) yield header + body;
    } finally { reader.releaseLock(); }
  }

  function post(text, filename) {
    return new Promise((resolve, reject) => {
      const requestId = crypto.randomUUID();
      const frame = document.createElement('iframe');
      frame.name = `creator-${requestId}`; frame.hidden = true;
      const postForm = document.createElement('form');
      postForm.method = 'POST'; postForm.action = endpoint; postForm.target = frame.name;
      const bytes = encoder.encode(text);
      let binary = '';
      for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i+8192));
      const fields = {action: 'uploadCreatorChunk', filename, csvBase64: btoa(binary), requestId, replyOrigin: location.origin};
      for (const [name, value] of Object.entries(fields)) {
        const field = document.createElement('input'); field.type = 'hidden'; field.name = name; field.value = value; postForm.append(field);
      }
      const cleanup = () => { clearTimeout(timer); window.removeEventListener('message', receive); postForm.remove(); frame.remove(); };
      const receive = event => {
        if (!/^https:\/\/(?:script\.google\.com|(?:[a-z0-9-]+\.)?googleusercontent\.com)$/.test(event.origin)) return;
        if (event.data?.requestId !== requestId) return;
        cleanup();
        event.data.ok ? resolve(event.data) : reject(new Error(event.data.error || 'Upload not confirmed.'));
      };
      const timer = setTimeout(() => { cleanup(); reject(new Error('No upload confirmation. Update the Apps Script upload handler before retrying.')); }, 120000);
      window.addEventListener('message', receive);
      document.body.append(frame, postForm); postForm.submit();
    });
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const files = [...input.files];
    if (!files.length || files.some(f => !/\.csv$/i.test(f.name))) { status.textContent = 'Choose one or more CSV files.'; return; }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true; input.disabled = true;
    let confirmed = 0;
    const session = new Date().toISOString().replace(/[-:.]/g, '') + '-' + crypto.randomUUID();
    try {
      for (let index = 0; index < files.length; index++) {
        let part = 0;
        for await (const text of chunks(files[index])) {
          const name = `${session}-${String(index).padStart(4,'0')}-${String(part++).padStart(6,'0')}.csv`;
          status.textContent = `Uploading ${files[index].name}, part ${part}… ${confirmed} confirmed.`;
          await post(text, name); confirmed++;
        }
        if (!part) throw new Error(`${files[index].name} contains no campaign rows.`);
      }
      status.textContent = `${confirmed} CSV parts confirmed. Run “Update Influencer Opportunities” to process the 100-ASIN test cohort.`;
      form.reset(); label.textContent = 'Choose CSV files';
    } catch (error) { status.textContent = `${error.message} ${confirmed} parts already saved; reruns deduplicate campaigns. Complete the upload before starting a scan.`; }
    finally { button.disabled = false; input.disabled = false; }
  });
})();
