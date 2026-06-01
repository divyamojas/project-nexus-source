-- Composite index on book_loans(book_id, status) to speed the borrowed_by
-- correlated subquery in ENRICHED_BOOKS_SQL, which filters both columns.
-- Previously only idx_book_loans_book_id existed, requiring a post-index status filter.
CREATE INDEX IF NOT EXISTS idx_book_loans_book_status
  ON book_loans(book_id, status);
