'use client';

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

interface FAQ {
  id: number;
  question: string;
  answer: string;
  tags: string[];
  is_active: boolean;
  created_at: string;
  embedding: number[] | null;
}

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const TENANT_ID = 1;

async function fetchFaqs(page: number) {
  const res = await fetch(`${API}/api/faqs?page=${page}`, {
    headers: { 'X-Tenant-ID': String(TENANT_ID) },
  });
  return res.json() as Promise<{ items: FAQ[]; total: number }>;
}

export default function KnowledgePage() {
  const [page, setPage] = useState(1);
  const queryClient = useQueryClient();

  const { data, isFetching } = useQuery({
    queryKey: ['faqs', page],
    queryFn: () => fetchFaqs(page),
  });

  const faqs = data?.items ?? [];
  const total = data?.total ?? 0;

  // Form state
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [tags, setTags] = useState('');

  // Import state
  const [vectorize, setVectorize] = useState(true);
  const [backfilling, setBackfilling] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);

  function openCreate() {
    setEditId(null);
    setQuestion('');
    setAnswer('');
    setTags('');
    setVectorize(true);
    setShowForm(true);
  }

  function openEdit(faq: FAQ) {
    setEditId(faq.id);
    setQuestion(faq.question);
    setAnswer(faq.answer);
    setTags(faq.tags.join(', '));
    setShowForm(true);
  }

  async function handleSave() {
    const tagList = tags.split(',').map(t => t.trim()).filter(Boolean);
    const body = JSON.stringify({ question, answer, tags: tagList, vectorize });

    if (editId) {
      await fetch(`${API}/api/faqs/${editId}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Tenant-ID': String(TENANT_ID) }, body,
      });
    } else {
      await fetch(`${API}/api/faqs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Tenant-ID': String(TENANT_ID) }, body,
      });
    }
    setShowForm(false);
    queryClient.invalidateQueries({ queryKey: ['faqs'] });
  }

  async function handleBackfill() {
    if (!confirm('将为所有向量为空的 FAQ 生成语义向量，确认？')) return;
    setBackfilling(true);
    try {
      const res = await fetch(`${API}/api/faqs/backfill`, {
        method: 'POST', headers: { 'X-Tenant-ID': String(TENANT_ID) },
      });
      const result = await res.json();
      alert(`向量化完成：${result.success}/${result.total} 成功`);
      queryClient.invalidateQueries({ queryKey: ['faqs'] });
    } catch {
      alert('请求失败');
    } finally {
      setBackfilling(false);
    }
  }

  async function handleVectorize(faqId: number) {
    await fetch(`${API}/api/faqs/${faqId}/vectorize`, {
      method: 'POST', headers: { 'X-Tenant-ID': String(TENANT_ID) },
    });
    queryClient.invalidateQueries({ queryKey: ['faqs'] });
  }

  async function handleDelete(id: number) {
    if (!confirm('确认删除？')) return;
    await fetch(`${API}/api/faqs/${id}`, {
      method: 'DELETE', headers: { 'X-Tenant-ID': String(TENANT_ID) },
    });
    queryClient.invalidateQueries({ queryKey: ['faqs'] });
  }

  async function handleImport() {
    if (!file) return;
    setImporting(true);
    const form = new FormData();
    form.append('file', file);

    const res = await fetch(`${API}/api/faqs/import`, {
      method: 'POST', headers: { 'X-Tenant-ID': String(TENANT_ID) }, body: form,
    });
    const result = await res.json();
    alert(`导入完成：${result.success}/${result.total} 条成功`);
    setFile(null);
    setImporting(false);
    queryClient.invalidateQueries({ queryKey: ['faqs'] });
  }

  const totalPages = Math.ceil(total / 20);

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>
          知识库
          {isFetching && <span style={{ marginLeft: 10, fontSize: 12, color: '#999', fontWeight: 400 }}>刷新中...</span>}
        </h2>
        <div style={{ display: 'flex', gap: 12 }}>
          <button
            onClick={handleBackfill}
            disabled={backfilling}
            style={{ padding: '6px 14px', border: '1px solid #d9d9d9', borderRadius: 6, fontSize: 13, background: '#fff', cursor: 'pointer' }}
          >
            {backfilling ? '向量化中...' : '一键向量化'}
          </button>
          <label style={{ cursor: 'pointer', padding: '6px 14px', border: '1px solid #d9d9d9', borderRadius: 6, fontSize: 13, background: '#fff' }}>
            {file ? file.name : '导入 CSV'}
            <input type="file" accept=".csv" hidden onChange={e => setFile(e.target.files?.[0] || null)} />
          </label>
          {file && (
            <button onClick={handleImport} disabled={importing} style={btnStyle}>
              {importing ? '导入中...' : '确认导入'}
            </button>
          )}
          <button onClick={openCreate} style={{ ...btnStyle, background: '#1677ff', color: '#fff', border: 'none' }}>
            + 新增
          </button>
        </div>
      </div>

      {/* FAQ List */}
      <div style={{ background: '#fff', borderRadius: 8 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #f0f0f0' }}>
              <th style={thStyle}>问题</th>
              <th style={thStyle}>回答</th>
              <th style={thStyle}>标签</th>
              <th style={{ ...thStyle, width: 80 }}>向量化</th>
              <th style={{ ...thStyle, width: 160 }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {faqs.map(faq => (
              <tr key={faq.id} style={{ borderBottom: '1px solid #f5f5f5' }}>
                <td style={tdStyle}>{faq.question}</td>
                <td style={{ ...tdStyle, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {faq.answer}
                </td>
                <td style={tdStyle}>
                  {faq.tags.map(t => (
                    <span key={t} style={{
                      display: 'inline-block', padding: '2px 6px', margin: '2px 4px 2px 0',
                      background: '#f0f0f0', borderRadius: 4, fontSize: 12, color: '#666',
                    }}>{t}</span>
                  ))}
                </td>
                <td style={tdStyle}>
                  {faq.embedding ? (
                    <span style={{ color: '#52c41a', fontSize: 12 }}>已向量化</span>
                  ) : (
                    <button onClick={() => handleVectorize(faq.id)} style={{ ...linkBtn, fontSize: 12 }}>向量化</button>
                  )}
                </td>
                <td style={tdStyle}>
                  <button onClick={() => openEdit(faq)} style={linkBtn}>编辑</button>
                  <button onClick={() => handleDelete(faq.id)} style={{ ...linkBtn, color: '#ff4d4f' }}>删除</button>
                </td>
              </tr>
            ))}
            {!isFetching && faqs.length === 0 && (
              <tr><td colSpan={5} style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无数据</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ marginTop: 16, textAlign: 'center' }}>
          <button disabled={page <= 1} onClick={() => setPage(page - 1)} style={pageBtn}>上一页</button>
          <span style={{ margin: '0 12px', fontSize: 14, color: '#666' }}>{page} / {totalPages}</span>
          <button disabled={page >= totalPages} onClick={() => setPage(page + 1)} style={pageBtn}>下一页</button>
        </div>
      )}

      {/* Form Modal */}
      {showForm && (
        <div onClick={() => setShowForm(false)} style={modalOverlay}>
          <div onClick={e => e.stopPropagation()} style={modalContent}>
            <h3 style={{ marginTop: 0 }}>{editId ? '编辑 FAQ' : '新增 FAQ'}</h3>
            <input placeholder="问题" value={question} onChange={e => setQuestion(e.target.value)} style={field} />
            <textarea placeholder="回答" value={answer} onChange={e => setAnswer(e.target.value)} rows={4} style={field} />
            <input placeholder="标签（逗号分隔）" value={tags} onChange={e => setTags(e.target.value)} style={field} />
            {!editId && (
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, marginBottom: 12, cursor: 'pointer' }}>
                <input type="checkbox" checked={vectorize} onChange={e => setVectorize(e.target.checked)} />
                生成语义向量
              </label>
            )}
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', marginTop: 16 }}>
              <button onClick={() => setShowForm(false)} style={{ ...btnStyle, background: '#fff', border: '1px solid #d9d9d9' }}>取消</button>
              <button onClick={handleSave} style={{ ...btnStyle, background: '#1677ff', color: '#fff', border: 'none' }}>保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const btnStyle: React.CSSProperties = { padding: '6px 16px', borderRadius: 6, fontSize: 13, cursor: 'pointer' };
const thStyle: React.CSSProperties = { textAlign: 'left', padding: '10px 16px', fontWeight: 500, color: '#999', fontSize: 13 };
const tdStyle: React.CSSProperties = { padding: '10px 16px' };
const linkBtn: React.CSSProperties = { background: 'none', border: 'none', cursor: 'pointer', fontSize: 13, padding: 0, marginRight: 12 };
const pageBtn: React.CSSProperties = { padding: '4px 12px', borderRadius: 4, border: '1px solid #d9d9d9', background: '#fff', cursor: 'pointer', fontSize: 13 };
const field: React.CSSProperties = { display: 'block', width: '100%', padding: '8px 12px', marginBottom: 12, border: '1px solid #d9d9d9', borderRadius: 6, fontSize: 14, boxSizing: 'border-box' };
const modalOverlay: React.CSSProperties = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 };
const modalContent: React.CSSProperties = { background: '#fff', padding: 24, borderRadius: 8, width: 480, maxHeight: '80vh', overflow: 'auto' };
