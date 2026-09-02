import { useState } from 'react';
import type { Key, ReactNode, SyntheticEvent } from 'react';

export type ChartDataColumn<Row> = {
  key: string;
  header: ReactNode;
  dataLabel?: string;
  rowHeader?: boolean;
  render: (row: Row) => ReactNode;
};

export function ChartDataTable<Row>({
  label,
  summary = '查看数据表',
  columns,
  rows,
  getRowKey,
  emptyMessage = '这里还没有可显示的数据。',
  alwaysOpen = false,
}: {
  label: string;
  summary?: string;
  columns: ChartDataColumn<Row>[];
  rows: Row[];
  getRowKey: (row: Row, index: number) => Key;
  emptyMessage?: string;
  alwaysOpen?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  const handleToggle = (event: SyntheticEvent<HTMLDetailsElement>) => {
    setExpanded(event.currentTarget.open);
  };

  const content = rows.length ? (
    <table className="chart-data-table">
      <caption>{label}</caption>
      <thead><tr>{columns.map((column) => <th key={column.key} scope="col">{column.header}</th>)}</tr></thead>
      <tbody>{rows.map((row, rowIndex) => (
        <tr key={getRowKey(row, rowIndex)}>{columns.map((column) => {
          const cell = column.render(row);
          const dataLabel = column.dataLabel ?? (typeof column.header === 'string' ? column.header : '');
          return column.rowHeader
            ? <th key={column.key} scope="row" data-label={dataLabel}>{cell}</th>
            : <td key={column.key} data-label={dataLabel}>{cell}</td>;
        })}</tr>
      ))}</tbody>
    </table>
  ) : <p className="chart-data-empty">{emptyMessage}</p>;

  if (alwaysOpen) return <div className="chart-data-table-wrap chart-data-table-standalone">{content}</div>;

  return (
    <details className="chart-data-disclosure" onToggle={handleToggle}>
      <summary role="button" aria-expanded={expanded}>{summary}</summary>
      {expanded && <div className="chart-data-table-wrap">{content}</div>}
    </details>
  );
}
