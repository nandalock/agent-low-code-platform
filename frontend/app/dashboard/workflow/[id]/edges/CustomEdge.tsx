'use client';

import { useState } from 'react';
import { BaseEdge, EdgeLabelRenderer, getBezierPath, useReactFlow, type EdgeProps } from '@xyflow/react';
import { X } from 'lucide-react';

export function CustomEdge({ id, sourceX, sourceY, targetX, targetY, selected }: EdgeProps) {
  const [hover, setHover] = useState(false);
  const { setEdges } = useReactFlow();

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX: sourceX - 5,
    sourceY,
    targetX: targetX + 5,
    targetY,
    curvature: 0.2,
  });

  const showDelete = hover || selected;

  return (
    <>
      <g onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
        <BaseEdge
          id={id}
          path={edgePath}
          style={{
            stroke: selected ? '#3370FF' : '#C9CDD4',
            strokeWidth: selected ? 2.5 : 2,
          }}
          interactionWidth={20}
        />
      </g>
      {showDelete && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: 'all',
            }}
          >
            <button
              onClick={() => setEdges(eds => eds.filter(e => e.id !== id))}
              style={{
                width: 20,
                height: 20,
                borderRadius: '50%',
                border: '1px solid #E5E6EB',
                background: '#fff',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: '0 1px 4px rgba(0,0,0,0.1)',
                padding: 0,
              }}
            >
              <X size={12} color="#86909C" />
            </button>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
