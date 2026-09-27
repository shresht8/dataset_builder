// Row comment thread + read-only edit history (design §4, GL-2-7). Opened
// per row from the grid. Escape closes it, same pattern as DetailDrawer.
import { useEffect, useState, type KeyboardEvent } from 'react'
import { ApiError, createComment, listComments, listEdits } from '../api/client'
import type { RowComment, RowEdit, User } from '../api/types'

interface CommentsPanelProps {
  datasetId: string
  rowId: string
  currentUserId: string
  users: User[] | null
  onClose: () => void
}

function userLabel(userId: string | null, currentUserId: string, users: User[] | null): string {
  if (userId === null) return 'system'
  if (userId === currentUserId) return 'You'
  return users?.find((u) => u.id === userId)?.display_name ?? `User ${userId.slice(0, 8)}`
}

export function CommentsPanel({ datasetId, rowId, currentUserId, users, onClose }: CommentsPanelProps) {
  const [tab, setTab] = useState<'comments' | 'history'>('comments')
  const [comments, setComments] = useState<RowComment[] | null>(null)
  const [edits, setEdits] = useState<RowEdit[] | null>(null)
  const [body, setBody] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [posting, setPosting] = useState(false)

  useEffect(() => {
    listComments(datasetId, rowId)
      .then(setComments)
      .catch(() => setComments([]))
    listEdits(datasetId, rowId)
      .then(setEdits)
      .catch(() => setEdits([]))
  }, [datasetId, rowId])

  async function submit() {
    const trimmed = body.trim()
    if (!trimmed) return
    setPosting(true)
    setError(null)
    try {
      const created = await createComment(datasetId, rowId, trimmed)
      setComments((prev) => [...(prev ?? []), created])
      setBody('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not post comment')
    } finally {
      setPosting(false)
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
    }
  }

  return (
    <div className="drawer-backdrop" onKeyDown={handleKeyDown}>
      <div className="drawer" role="dialog" aria-modal="true" aria-label="Row activity">
        <div className="drawer-header">
          <span>Row activity</span>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="tab-bar">
          <button type="button" className={tab === 'comments' ? 'tab-active' : ''} onClick={() => setTab('comments')}>
            Comments
          </button>
          <button type="button" className={tab === 'history' ? 'tab-active' : ''} onClick={() => setTab('history')}>
            History
          </button>
        </div>
        {tab === 'comments' && (
          <>
            <ul className="comment-list">
              {(comments ?? []).map((c) => (
                <li key={c.id}>
                  <div className="comment-meta">
                    <strong>{userLabel(c.user_id, currentUserId, users)}</strong>{' '}
                    <span>{new Date(c.created_at).toLocaleString()}</span>
                  </div>
                  <p>{c.body}</p>
                </li>
              ))}
              {comments !== null && comments.length === 0 && <li>No comments yet.</li>}
            </ul>
            <div className="comment-form">
              <textarea
                autoFocus
                value={body}
                onChange={(event) => setBody(event.target.value)}
                placeholder="Add a comment"
              />
              {error && <p className="error">{error}</p>}
              <button type="button" onClick={() => void submit()} disabled={posting || !body.trim()}>
                Post
              </button>
            </div>
          </>
        )}
        {tab === 'history' && (
          <ul className="comment-list">
            {(edits ?? []).map((e) => (
              <li key={e.id}>
                <div className="comment-meta">
                  <strong>{userLabel(e.user_id, currentUserId, users)}</strong>{' '}
                  <span>{new Date(e.at).toLocaleString()}</span>
                </div>
                <p>
                  {e.field}: {JSON.stringify(e.old_value)} &rarr; {JSON.stringify(e.new_value)}
                </p>
              </li>
            ))}
            {edits !== null && edits.length === 0 && <li>No history yet.</li>}
          </ul>
        )}
      </div>
    </div>
  )
}
