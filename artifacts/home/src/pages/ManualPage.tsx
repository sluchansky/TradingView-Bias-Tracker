/**
 * Operator Manual — rendered from OPERATOR_MANUAL.md (bundled at build time).
 * Uses a lightweight inline markdown → HTML converter; no external dependencies.
 */
import manualRaw from '../docs/OPERATOR_MANUAL.md?raw';

// ── Lightweight markdown → HTML ───────────────────────────────────────────────

function escHtml(s: string) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

function inlineMarkdown(s: string): string {
  // code before bold/italic
  s = s.replace(/`([^`]+)`/g, (_,c) => `<code style="background:#0d2040;padding:1px 5px;border-radius:3px;font-size:0.88em;color:#7dd3fc;font-family:monospace">${escHtml(c)}</code>`);
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" style="color:#38bdf8;text-decoration:underline">$1</a>');
  return s;
}

function renderMarkdown(md: string): string {
  const lines = md.split('\n');
  const out: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // ── Fenced code block ──────────────────────────────────────────────────
    if (line.startsWith('```')) {
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(escHtml(lines[i]));
        i++;
      }
      out.push(`<pre style="background:#061020;border:1px solid #1e3a5f;border-radius:6px;padding:12px 16px;overflow-x:auto;font-size:0.82em;line-height:1.6;color:#94a3b8;font-family:monospace;margin:12px 0">${codeLines.join('\n')}</pre>`);
      i++; // skip closing ```
      continue;
    }

    // ── Table ──────────────────────────────────────────────────────────────
    if (line.startsWith('|')) {
      const tableLines: string[] = [];
      while (i < lines.length && lines[i].startsWith('|')) {
        tableLines.push(lines[i]);
        i++;
      }
      const rows = tableLines.filter(l => !/^\|[\s|:-]+\|$/.test(l.trim()));
      const tableHtml = rows.map((row, ri) => {
        const cells = row.split('|').slice(1, -1).map(c => c.trim());
        const tag = ri === 0 ? 'th' : 'td';
        const cellStyle = ri === 0
          ? 'background:#0d2040;color:#7dd3fc;font-weight:700;font-size:0.78em;letter-spacing:0.05em;padding:7px 12px;text-align:left;border:1px solid #1e3a5f'
          : 'padding:6px 12px;border:1px solid #1a2e4a;color:#cbd5e1;font-size:0.85em;vertical-align:top';
        return `<tr>${cells.map(c => `<${tag} style="${cellStyle}">${inlineMarkdown(c)}</${tag}>`).join('')}</tr>`;
      }).join('');
      out.push(`<div style="overflow-x:auto;margin:12px 0"><table style="width:100%;border-collapse:collapse;border:1px solid #1e3a5f;border-radius:6px;overflow:hidden">${tableHtml}</table></div>`);
      continue;
    }

    // ── HR ─────────────────────────────────────────────────────────────────
    if (/^---+$/.test(line.trim())) {
      out.push(`<hr style="border:none;border-top:1px solid #1e3a5f;margin:24px 0" />`);
      i++; continue;
    }

    // ── Headers ────────────────────────────────────────────────────────────
    const h1 = line.match(/^# (.+)/);
    if (h1) {
      const id = slugify(h1[1]);
      out.push(`<h1 id="${id}" style="font-size:1.6em;font-weight:800;color:#e2e8f0;margin:32px 0 8px;letter-spacing:-0.02em;border-bottom:2px solid #1e3a5f;padding-bottom:10px;scroll-margin-top:52px">${inlineMarkdown(escHtml(h1[1]))}</h1>`);
      i++; continue;
    }
    const h2 = line.match(/^## (.+)/);
    if (h2) {
      const id = slugify(h2[1]);
      out.push(`<h2 id="${id}" style="font-size:1.2em;font-weight:700;color:#7dd3fc;margin:28px 0 6px;letter-spacing:0.02em;scroll-margin-top:52px">${inlineMarkdown(escHtml(h2[1]))}</h2>`);
      i++; continue;
    }
    const h3 = line.match(/^### (.+)/);
    if (h3) {
      const id = slugify(h3[1]);
      out.push(`<h3 id="${id}" style="font-size:1em;font-weight:700;color:#94a3b8;margin:20px 0 4px;text-transform:uppercase;letter-spacing:0.06em;scroll-margin-top:52px">${inlineMarkdown(escHtml(h3[1]))}</h3>`);
      i++; continue;
    }

    // ── Blockquote ─────────────────────────────────────────────────────────
    if (line.startsWith('> ')) {
      out.push(`<blockquote style="border-left:3px solid #38bdf8;margin:10px 0;padding:8px 14px;background:#061828;border-radius:0 6px 6px 0;color:#94a3b8;font-size:0.9em">${inlineMarkdown(escHtml(line.slice(2)))}</blockquote>`);
      i++; continue;
    }

    // ── Unordered list items ───────────────────────────────────────────────
    if (/^(\s*)[-*] /.test(line)) {
      const listLines: string[] = [];
      while (i < lines.length && /^(\s*)[-*] /.test(lines[i])) {
        listLines.push(lines[i]);
        i++;
      }
      const items = listLines.map(l => {
        const content = l.replace(/^\s*[-*] /, '');
        const indent = (l.match(/^(\s*)/)?.[1].length ?? 0) > 0;
        return `<li style="margin:4px 0;color:#cbd5e1;font-size:0.9em;${indent ? 'margin-left:20px' : ''}">${inlineMarkdown(escHtml(content))}</li>`;
      }).join('');
      out.push(`<ul style="padding-left:20px;margin:8px 0;list-style:disc">${items}</ul>`);
      continue;
    }

    // ── Blank line → paragraph separator ──────────────────────────────────
    if (line.trim() === '') {
      i++; continue;
    }

    // ── Plain paragraph ────────────────────────────────────────────────────
    out.push(`<p style="margin:6px 0;color:#cbd5e1;font-size:0.9em;line-height:1.7">${inlineMarkdown(escHtml(line))}</p>`);
    i++;
  }

  return out.join('\n');
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function ManualPage() {
  const html = renderMarkdown(manualRaw);

  return (
    <div style={{
      minHeight: '100vh',
      background: '#020c1b',
      color: '#e2e8f0',
      fontFamily: '-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
    }}>
      {/* Top bar */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 10,
        background: '#040d1e',
        borderBottom: '1px solid #1e3a5f',
        padding: '10px 24px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 18 }}>📖</span>
          <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: '0.06em', color: '#7dd3fc' }}>
            OPERATOR MANUAL
          </span>
        </div>
        <a
          href="/"
          style={{
            fontSize: 11, fontWeight: 700, color: '#64748b', letterSpacing: '0.06em',
            textDecoration: 'none', padding: '5px 10px',
            border: '1px solid #1e3a5f', borderRadius: 5,
          }}
        >
          ← BACK TO BRAIN
        </a>
      </div>

      {/* Manual content */}
      <div style={{ maxWidth: 860, margin: '0 auto', padding: '32px 24px 80px' }}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}
