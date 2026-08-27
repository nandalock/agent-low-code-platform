'use client';

import { useState } from 'react';
import { T } from '@/app/theme';

export type BlockEntity = {
  name: string;
  pattern: string;
  penalty: number;
};

export type CachePolicyData = {
  base_score?: number;
  cacheable_intents?: string[];
  block_entities?: BlockEntity[];
  content_hint?: string;
  scorer_weights?: Record<string, number>;
  min_score?: number;
  semantic_min_score?: number;
};

interface Props {
  value: CachePolicyData;
  onChange: (v: CachePolicyData) => void;
  /** node-level min/semantic_min scores, for decision preview only */
  minScore?: number;
  semanticMinScore?: number;
  /** show base_score + content_hint fields (agent-level), false for node override panel */
  showBaseConfig?: boolean;
}

const SCORER_META: { key: string; label: string; desc: string }[] = [
  { key: 'agent_config', label: '基础评分', desc: '基于 agent 类型的缓存倾向' },
  { key: 'intent', label: '意图匹配', desc: '意图是否在可缓存白名单中' },
  { key: 'entity', label: '敏感信息', desc: '检测问题中的敏感实体并封杀' },
  { key: 'quality', label: '回答质量', desc: '排除错误、兜底、转人工回复' },
];

const ENTITY_PRESETS: BlockEntity[] = [
  { name: '手机号', pattern: String.raw`\b1[3-9]\d{9}\b`, penalty: 0.0 },
  { name: '身份证', pattern: String.raw`\b\d{6}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b`, penalty: 0.0 },
  { name: '订单号', pattern: String.raw`\b\d{15,20}\b`, penalty: 0.0 },
  { name: '运单号', pattern: String.raw`\b\d{15,20}\b`, penalty: 0.0 },
  { name: '日期', pattern: String.raw`\b\d{4}-\d{2}-\d{2}\b`, penalty: 0.2 },
  { name: '金额', pattern: String.raw`\d+\.?\d*\s*元`, penalty: 0.2 },
  { name: '地址', pattern: String.raw`(北京|上海|广州|深圳|杭州|成都|武汉|南京|重庆|天津|苏州|西安).{0,10}(区|路|街|楼|号)`, penalty: 0.3 },
];

const S = { xs: 4, sm: 8, md: 12, base: 16, lg: 20, xl: 24 };

const rangeHint: React.CSSProperties = { display: 'flex', justifyContent: 'space-between', fontSize: 10, color: T.tertiary, marginTop: -4 };
const stLabel: React.CSSProperties = { fontSize: 12, fontWeight: 500, color: T.text, marginBottom: 2 };
const inputStyle: React.CSSProperties = {
  padding: '4px 8px', borderRadius: 4, border: `1px solid ${T.border}`,
  fontSize: 11, color: T.text, background: T.surface, fontFamily: 'inherit', outline: 'none',
};

export default function CachePolicyEditor({ value, onChange, minScore = 0.40, semanticMinScore = 0.70, showBaseConfig = true }: Props) {
  const bs = value.base_score ?? 0.50;
  const intents: string[] = value.cacheable_intents || [];
  const blocked: BlockEntity[] = (value.block_entities || []).map((e: any) =>
    typeof e === 'string' ? { name: e, pattern: '', penalty: 0.0 } : e
  );
  const hint: string = value.content_hint || '';
  const sw: Record<string, number> = value.scorer_weights || {};
  const [showRules, setShowRules] = useState(false);
  const [showEntityForm, setShowEntityForm] = useState(false);
  const [newEntity, setNewEntity] = useState<BlockEntity>({ name: '', pattern: '', penalty: 0.0 });

  const allEnabled = Object.keys(sw).length === 0;
  const totalW = allEnabled ? 1.0 : Object.values(sw).reduce((a, b) => a + b, 0);

  function patch(p: Partial<CachePolicyData>) {
    onChange({ ...value, ...p });
  }

  function updateWeight(key: string, pct: number) {
    const weights: Record<string, number> = sw ? { ...sw } : {};
    if (pct <= 0) {
      delete weights[key];
    } else {
      weights[key] = pct / 100;
    }
    patch({ scorer_weights: Object.keys(weights).length > 0 ? weights : undefined });
  }

  function toggleScorer(key: string) {
    const weights: Record<string, number> = sw ? { ...sw } : {};
    if (weights[key]) {
      delete weights[key];
    } else {
      weights[key] = 0.20;
    }
    patch({ scorer_weights: Object.keys(weights).length > 0 ? weights : undefined });
  }

  function addIntent() {
    const v = prompt('添加意图名称（如 faq, knowledge, order_query）');
    if (v && v.trim()) patch({ cacheable_intents: [...intents, v.trim()] });
  }

  function removeIntent(it: string) {
    patch({ cacheable_intents: intents.filter(x => x !== it) });
  }

  function addEntity() {
    if (!newEntity.name.trim() || !newEntity.pattern.trim()) return;
    patch({ block_entities: [...blocked, { ...newEntity, penalty: newEntity.penalty }] });
    setNewEntity({ name: '', pattern: '', penalty: 0.0 });
    setShowEntityForm(false);
  }

  function removeEntity(idx: number) {
    patch({ block_entities: blocked.filter((_, i) => i !== idx) });
  }

  function fillPreset(p: BlockEntity) {
    setNewEntity({ ...p });
    setShowEntityForm(true);
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: S.md, fontSize: 12 }}>
      {/* ── base_score ── */}
      {showBaseConfig && (
        <>
          <div>
            <div style={stLabel}>缓存倾向: {bs >= 0.85 ? '非常适合' : bs >= 0.50 ? '一般' : bs > 0 ? '不适合' : '永不缓存'} (base_score={bs.toFixed(2)})</div>
            <input type="range" min={0} max={1} step={0.05} value={bs}
              onChange={e => patch({ base_score: parseFloat(e.target.value) })}
              style={{ width: '100%', accentColor: T.accent }} />
            <div style={rangeHint}><span>0.00 永不缓存</span><span>1.00 尽量缓存</span></div>
          </div>

          {/* ── content_hint ── */}
          <div>
            <div style={stLabel}>内容类型</div>
            <select value={hint} onChange={e => patch({ content_hint: e.target.value || undefined })}
              style={{
                width: '100%', padding: '6px 10px', borderRadius: 6,
                border: `1px solid ${T.border}`, fontSize: 12, color: T.text,
                background: T.surface, fontFamily: 'inherit',
              }}>
              <option value="">未指定（不加成）</option>
              <option value="knowledge">知识型 — 适合缓存 (+0.10)</option>
              <option value="operation">操作型 — 不太适合缓存 (-0.15)</option>
              <option value="realtime">实时型 — 不适合缓存 (-0.25)</option>
            </select>
          </div>
        </>
      )}

      {/* ── scorer_weights ── */}
      <div>
        <div style={{ ...stLabel, marginBottom: S.sm }}>
          评分器 ({allEnabled ? '全部启用 · 均权' : `${Object.keys(sw).length}/4 启用`})
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {SCORER_META.map(s => {
            const enabled = allEnabled || (sw[s.key] ?? 0) > 0;
            const rawW = allEnabled ? 0.25 : (sw[s.key] ?? 0);
            const pct = Math.round(rawW * 100);
            const normW = allEnabled ? 0.25 : (totalW > 0 ? rawW / totalW : 0);
            return (
              <div key={s.key} style={{
                padding: '6px 8px', borderRadius: 6,
                background: enabled ? T.accentBg : T.bg,
                border: `1px solid ${enabled ? T.accent + '30' : T.border}`,
                opacity: enabled ? 1 : 0.55,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <input type="checkbox" checked={enabled}
                    onChange={() => toggleScorer(s.key)}
                    style={{ accentColor: T.accent, width: 13, height: 13, cursor: 'pointer', flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 11, fontWeight: 500, color: T.text }}>{s.label}</div>
                    <div style={{ fontSize: 10, color: T.tertiary }}>{s.desc}</div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 3, flexShrink: 0 }}>
                    <input type="range" min={0} max={100} step={5} value={pct}
                      disabled={!enabled}
                      onChange={e => updateWeight(s.key, parseInt(e.target.value))}
                      style={{ width: 52, accentColor: T.accent, cursor: enabled ? 'pointer' : 'default' }} />
                    <span style={{ fontSize: 10, fontWeight: 600, color: T.text, width: 30, textAlign: 'right' }}>
                      {(normW * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── 评分规则说明（可折叠）── */}
      <div>
        <button
          onClick={() => setShowRules(!showRules)}
          style={{
            display: 'flex', alignItems: 'center', gap: 4,
            border: 'none', background: 'transparent', cursor: 'pointer',
            fontSize: 11, color: T.secondary, padding: 0, fontFamily: 'inherit',
          }}>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            style={{ transform: showRules ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.15s' }}>
            <polyline points="9 18 15 12 9 6" />
          </svg>
          评分规则说明
        </button>
        {showRules && (
          <div style={{ marginTop: S.sm, padding: S.sm, background: T.bg, borderRadius: 6, fontSize: 11, lineHeight: 1.7, color: T.secondary }}>
            {SCORER_META.map(s => (
              <div key={s.key} style={{ marginBottom: S.sm }}>
                <div style={{ fontWeight: 600, color: T.text, marginBottom: 2 }}>{s.label}</div>
                {s.key === 'agent_config' && (
                  <ul style={{ margin: 0, paddingLeft: 14 }}>
                    <li>读取 <b>base_score</b> 配置值作为本项评分</li>
                    <li>未配置 → <b>0.50</b>（中性）</li>
                    <li>&gt; 0.70 倾向缓存，&lt; 0.30 倾向不缓存</li>
                  </ul>
                )}
                {s.key === 'intent' && (
                  <ul style={{ margin: 0, paddingLeft: 14 }}>
                    <li>配置了意图白名单时：命中 → <b>0.90</b>，未命中 → <b>0.05</b></li>
                    <li>未配置白名单 → <b>0.50</b>（中性）</li>
                    <li>最终分 = 基础分 × <b>意图置信度</b>（由路由器的 confidence 决定）</li>
                  </ul>
                )}
                {s.key === 'entity' && (
                  <ul style={{ margin: 0, paddingLeft: 14 }}>
                    <li>读取 <b>block_entities</b> 配置，逐条用正则匹配问题文本</li>
                    <li>命中 → 取所有命中实体中最低的 <b>penalty</b> 值</li>
                    <li>未命中任何实体 → <b>1.0</b></li>
                    <li>未配置封杀列表 → <b>1.0</b>（不封杀）</li>
                  </ul>
                )}
                {s.key === 'quality' && (
                  <ul style={{ margin: 0, paddingLeft: 14 }}>
                    <li>LLM 调用异常 → <b style={{ color: '#F53F3F' }}>0.0</b>（硬否决，强制不缓存）</li>
                    <li>返回兜底回复 → <b style={{ color: '#F53F3F' }}>0.0</b>（硬否决）</li>
                    <li>转人工 → <b style={{ color: '#F53F3F' }}>0.0</b>（硬否决）</li>
                    <li>答案为空（预评估阶段）→ <b>0.50</b></li>
                    <li>答案 &lt; 50 字符 → <b>0.30</b></li>
                    <li>包含引用来源 → <b>0.90</b></li>
                    <li>正常回复 → <b>0.70</b></li>
                  </ul>
                )}
              </div>
            ))}
            <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: S.sm, marginTop: S.sm }}>
              <div style={{ fontWeight: 600, color: T.text, marginBottom: 2 }}>内容类型加成（非评分器）</div>
              <ul style={{ margin: 0, paddingLeft: 14 }}>
                <li>在评分器加权综合分基础上直接加/减</li>
                <li>知识型 → <b style={{ color: '#00B42A' }}>+0.10</b>，操作型 → <b style={{ color: '#F5A623' }}>-0.15</b>，实时型 → <b style={{ color: '#F53F3F' }}>-0.25</b></li>
                <li>综合分 = max(0, min(1, 加权分 + 加成))</li>
              </ul>
            </div>
          </div>
        )}
      </div>

      {/* ── 意图白名单 ── */}
      <div>
        <div style={stLabel}>意图白名单</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {intents.length === 0 && <span style={{ fontSize: 11, color: T.tertiary }}>未配置（所有意图中性评分）</span>}
          {intents.map(it => (
            <span key={it} style={{
              fontSize: 11, padding: '2px 8px', borderRadius: 10,
              background: '#E8F5E9', color: '#2E7D32',
              display: 'inline-flex', alignItems: 'center', gap: 4,
            }}>
              {it}
              <button onClick={() => removeIntent(it)} style={{
                border: 'none', background: 'transparent', cursor: 'pointer', padding: 0, display: 'flex', color: '#2E7D32',
              }}><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
            </span>
          ))}
          <button onClick={addIntent} style={{
            width: 22, height: 22, borderRadius: 11, border: `1px dashed ${T.border}`,
            background: 'transparent', cursor: 'pointer', display: 'flex',
            alignItems: 'center', justifyContent: 'center', color: T.secondary,
          }}><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button>
        </div>
      </div>

      {/* ── 封杀实体 ── */}
      <div>
        <div style={stLabel}>封杀实体</div>

        {/* preset chips */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: S.sm }}>
          <span style={{ fontSize: 10, color: T.tertiary, marginRight: 2 }}>预设模板:</span>
          {ENTITY_PRESETS.map(p => (
            <button key={p.name} onClick={() => fillPreset(p)} style={{
              fontSize: 10, padding: '1px 8px', borderRadius: 10, cursor: 'pointer',
              border: `1px solid ${T.border}`, background: T.bg, color: T.secondary,
              fontFamily: 'inherit', lineHeight: '18px',
            }}>{p.name}</button>
          ))}
        </div>

        {/* entity list */}
        {blocked.length === 0 && !showEntityForm && (
          <div style={{ fontSize: 11, color: T.tertiary, marginBottom: 4 }}>未配置（不封杀任何实体）</div>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {blocked.map((e, idx) => (
            <div key={idx} style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '4px 8px', borderRadius: 6, background: '#FFF3E0',
              border: '1px solid #FFE0B2',
            }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 11, fontWeight: 500, color: '#E65100' }}>{e.name}</div>
                <div style={{ fontSize: 9, color: '#BF360C', fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  /{e.pattern}/
                </div>
              </div>
              <span style={{
                fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 8,
                background: e.penalty === 0 ? '#F53F3F20' : '#F5A62320',
                color: e.penalty === 0 ? '#F53F3F' : '#E65100',
              }}>{e.penalty.toFixed(1)}</span>
              <button onClick={() => removeEntity(idx)} style={{
                border: 'none', background: 'transparent', cursor: 'pointer', padding: 0, display: 'flex',
                flexShrink: 0,
              }}><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#E65100" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
            </div>
          ))}
        </div>

        {/* add entity form */}
        {showEntityForm && (
          <div style={{ marginTop: S.sm, padding: S.sm, background: T.bg, borderRadius: 6, display: 'flex', flexDirection: 'column', gap: S.xs }}>
            <div style={{ display: 'flex', gap: S.xs, alignItems: 'center' }}>
              <span style={{ fontSize: 10, color: T.secondary, flexShrink: 0, width: 28 }}>名称</span>
              <input value={newEntity.name} onChange={e => setNewEntity({ ...newEntity, name: e.target.value })}
                placeholder="如: 手机号" style={{ ...inputStyle, flex: 1 }} />
            </div>
            <div style={{ display: 'flex', gap: S.xs, alignItems: 'center' }}>
              <span style={{ fontSize: 10, color: T.secondary, flexShrink: 0, width: 28 }}>正则</span>
              <input value={newEntity.pattern} onChange={e => setNewEntity({ ...newEntity, pattern: e.target.value })}
                placeholder={String.raw`如: \b1[3-9]\d{9}\b`} style={{ ...inputStyle, flex: 1, fontFamily: 'monospace' }} />
            </div>
            <div style={{ display: 'flex', gap: S.xs, alignItems: 'center' }}>
              <span style={{ fontSize: 10, color: T.secondary, flexShrink: 0, width: 28 }}>惩罚</span>
              <input type="range" min={0} max={1} step={0.05} value={newEntity.penalty}
                onChange={e => setNewEntity({ ...newEntity, penalty: parseFloat(e.target.value) })}
                style={{ flex: 1, accentColor: T.accent }} />
              <span style={{ fontSize: 10, fontWeight: 600, color: T.text, width: 26, textAlign: 'right' }}>{newEntity.penalty.toFixed(2)}</span>
            </div>
            <div style={{ display: 'flex', gap: S.xs, justifyContent: 'flex-end' }}>
              <button onClick={() => { setShowEntityForm(false); setNewEntity({ name: '', pattern: '', penalty: 0.0 }); }}
                style={{ fontSize: 10, padding: '2px 10px', borderRadius: 4, border: `1px solid ${T.border}`, background: T.surface, color: T.secondary, cursor: 'pointer', fontFamily: 'inherit' }}>
                取消
              </button>
              <button onClick={addEntity} disabled={!newEntity.name.trim() || !newEntity.pattern.trim()}
                style={{ fontSize: 10, padding: '2px 10px', borderRadius: 4, border: 'none', background: T.accent, color: '#fff', cursor: 'pointer', fontFamily: 'inherit', opacity: !newEntity.name.trim() || !newEntity.pattern.trim() ? 0.5 : 1 }}>
                添加
              </button>
            </div>
          </div>
        )}

        {!showEntityForm && (
          <button onClick={() => setShowEntityForm(true)} style={{
            marginTop: S.xs, width: 22, height: 22, borderRadius: 11, border: `1px dashed ${T.border}`,
            background: 'transparent', cursor: 'pointer', display: 'flex',
            alignItems: 'center', justifyContent: 'center', color: T.secondary,
          }}><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button>
        )}
      </div>

      {/* ── 决策预览 ── */}
      <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: S.sm }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: T.text, marginBottom: 4 }}>决策预览</div>
        <div style={{ fontSize: 11, color: T.secondary, lineHeight: 1.8 }}>
          {allEnabled ? (
            <div style={{ color: T.tertiary, marginBottom: 2 }}>全部 4 个评分器均权启用</div>
          ) : (
            <div style={{ marginBottom: 2 }}>
              {SCORER_META.filter(s => (sw[s.key] ?? 0) > 0).map(s => {
                const w = sw[s.key];
                const normW = totalW > 0 ? w / totalW : 0;
                return <div key={s.key}>· {s.label} ×{(normW * 100).toFixed(0)}%</div>;
              })}
            </div>
          )}
          {hint && (
            <div style={{ marginBottom: 2, color: T.accent }}>
              · 内容类型加成: {hint === 'knowledge' ? '+0.10' : hint === 'operation' ? '-0.15' : hint === 'realtime' ? '-0.25' : '±0'} ({hint})
            </div>
          )}
          <div style={{ padding: '6px 8px', background: T.bg, borderRadius: 6, lineHeight: 1.8 }}>
            <div>综合分 ≥ <b>{semanticMinScore.toFixed(2)}</b> → <b style={{ color: '#00B42A' }}>语义缓存 (L1+L2)</b></div>
            <div>综合分 ≥ <b>{minScore.toFixed(2)}</b> → <b style={{ color: '#F5A623' }}>精确缓存 (L1)</b></div>
            <div>综合分 &lt; <b>{minScore.toFixed(2)}</b> → <b style={{ color: '#F53F3F' }}>不缓存</b></div>
          </div>
        </div>
      </div>
    </div>
  );
}
