'use client';

import { memo } from 'react';
import { Handle, Position, useReactFlow, type NodeProps, type Node } from '@xyflow/react';
import { Play, User, GitBranch, CheckCircle, Brain, Trash2, Database } from 'lucide-react';

const STYLE: Record<string, { bg:string; border:string; icon:React.ReactNode }> = {
  start:     { bg:'#E8FFEA', border:'#00B42A', icon:<Play size={14} color="#00B42A" /> },
  agent:     { bg:'#EEF2FF', border:'#3370FF', icon:<User size={14} color="#3370FF" /> },
  condition: { bg:'#FFF7E8', border:'#FF7D00', icon:<GitBranch size={14} color="#FF7D00" /> },
  router:    { bg:'#F3E8FF', border:'#9333EA', icon:<Brain size={14} color="#9333EA" /> },
  end:       { bg:'#FFF2F0', border:'#F53F3F', icon:<CheckCircle size={14} color="#F53F3F" /> },
};

export type CacheConfig = {
  enabled: boolean;
  strategy: 'exact' | 'semantic';
  threshold: number;
  ttl: number;
  min_score?: number;
  semantic_min_score?: number;
  min_answer_length?: number;
  max_question_length?: number;
  base_score?: number;
  cacheable_intents?: string[];
  block_entities?: string[];
  content_hint?: string;
  scorer_weights?: Record<string, number>;
};

export type CustomNodeData = {
  nodeType: 'start' | 'agent' | 'condition' | 'router' | 'end';
  label: string;
  description: string;
  config?: {
    agent_key?: string;
    field?: string;
    op?: string;
    value?: string;
    branches?: { label: string; target: string }[];
    cache?: CacheConfig;
  };
};

function BaseNode({ id, data, selected }: NodeProps<Node<CustomNodeData>>) {
  const { deleteElements } = useReactFlow();
  const s = STYLE[data.nodeType] || STYLE.agent;
  const isStart = data.nodeType === 'start';
  const isEnd   = data.nodeType === 'end';

  return (
    <div style={{
      position:'relative', background:'#fff', borderRadius:10, border:`2px solid ${selected?'#3370FF':s.border}`,
      minWidth:180, boxShadow:selected?'0 4px 16px rgba(51,112,255,0.15)':'0 1px 4px rgba(0,0,0,0.06)',
    }}>
      {selected && !isStart && !isEnd && (
        <button
          onClick={(e) => { e.stopPropagation(); deleteElements({ nodes: [{ id }] }); }}
          title="删除节点"
          style={{
            position:'absolute', top:-10, right:-10, width:22, height:22, borderRadius:11,
            background:'#F53F3F', border:'2px solid #fff', cursor:'pointer',
            display:'flex', alignItems:'center', justifyContent:'center',
            boxShadow:'0 1px 4px rgba(0,0,0,0.12)', zIndex:10,
          }}
        >
          <Trash2 size={11} color="#fff" />
        </button>
      )}
      {/* header */}
      <div style={{
        display:'flex', alignItems:'center', gap:8, padding:'10px 14px',
        background:s.bg, borderRadius:'8px 8px 0 0', borderBottom:`1px solid ${s.border}20`,
      }}>
        <div style={{ width:28, height:28, borderRadius:6, background:`${s.border}18`, display:'flex', alignItems:'center', justifyContent:'center' }}>
          {s.icon}
        </div>
        <div>
          <div style={{ fontSize:13, fontWeight:600, color:'#1D2129' }}>{data.label}</div>
          <div style={{ fontSize:10, color:'#86909C' }}>{data.nodeType}</div>
        </div>
      </div>
      {/* body */}
      {data.description && (
        <div style={{ padding:'8px 14px', fontSize:11, color:'#86909C', lineHeight:1.4 }}>
          {data.description}
        </div>
      )}
      {data.config?.cache?.enabled && (
        <div style={{
          margin:'0 8px 8px', padding:'4px 10px', borderRadius:6,
          background:'#E8FFEA', border:'1px solid #A7F0BA',
          display:'flex', alignItems:'center', gap:5, fontSize:10,
        }}>
          <Database size={10} color="#00B42A" />
          <span style={{ color:'#00B42A', fontWeight:600 }}>
            {data.config.cache.strategy === 'exact' ? '精确缓存' : '语义缓存'}
          </span>
          <span style={{ color:'#6FBF7C' }}>
            th={data.config.cache.threshold} · {data.config.cache.ttl >= 86400 ? `${data.config.cache.ttl / 86400}d` : `${(data.config.cache.ttl / 3600).toFixed(0)}h`}
          </span>
        </div>
      )}
      {/* handles */}
      {!isEnd   && <Handle type="source" position={Position.Right}  style={{ width:10, height:10, background:s.border, border:'2px solid #fff' }} />}
      {!isStart && <Handle type="target" position={Position.Left}   style={{ width:10, height:10, background:s.border, border:'2px solid #fff' }} />}
    </div>
  );
}

export const CustomNode = memo(BaseNode);
