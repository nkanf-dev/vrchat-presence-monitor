export type ChartSelection = {
  row: number;
  column: number;
  pinned: boolean;
};

export type ChartSelectionAction =
  | { type: 'hover'; row: number; column: number }
  | { type: 'leave' }
  | { type: 'tap'; row: number; column: number }
  | { type: 'focus'; row: number; column: number }
  | {
      type: 'move';
      rowDelta: number;
      columnDelta: number;
      rowCount: number;
      columnCount: number;
    }
  | { type: 'clear' };

export type PointerPosition = { x: number; y: number };

const clampIndex = (value: number, count: number) =>
  Math.max(0, Math.min(Math.max(0, count - 1), Math.round(value)));

export const reduceChartSelection = (
  selection: ChartSelection | null,
  action: ChartSelectionAction,
): ChartSelection | null => {
  switch (action.type) {
    case 'hover':
      if (selection?.pinned) return selection;
      return { row: action.row, column: action.column, pinned: false };
    case 'leave':
      return selection?.pinned ? selection : null;
    case 'tap':
      if (
        selection?.pinned
        && selection.row === action.row
        && selection.column === action.column
      ) return null;
      return { row: action.row, column: action.column, pinned: true };
    case 'focus':
      return { row: action.row, column: action.column, pinned: true };
    case 'move': {
      if (action.rowCount <= 0 || action.columnCount <= 0) return null;
      const row = clampIndex((selection?.row ?? 0) + action.rowDelta, action.rowCount);
      const column = clampIndex((selection?.column ?? 0) + action.columnDelta, action.columnCount);
      return { row, column, pinned: true };
    }
    case 'clear':
      return null;
  }
};

export const isTapGesture = (
  start: PointerPosition,
  end: PointerPosition,
  maximumDistance = 10,
) => Math.hypot(end.x - start.x, end.y - start.y) <= maximumDistance;
