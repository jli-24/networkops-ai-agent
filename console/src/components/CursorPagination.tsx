export function CursorPagination({ previous, next, onPrevious, onNext }: {
  previous: boolean;
  next: string | null | undefined;
  onPrevious: () => void;
  onNext: (cursor: string) => void;
}) {
  return <div className="pagination"><button type="button" disabled={!previous} onClick={onPrevious}>Previous page</button><button type="button" disabled={!next} onClick={() => next && onNext(next)}>Next page</button></div>;
}
