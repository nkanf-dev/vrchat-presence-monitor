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
}: {
  label: string;
  summary?: string;
  columns: ChartDataColumn<Row>[];
  rows: Row[];
  getRowKey: (row: Row, index: number) => Key;
  emptyMessage?: string;
}) {
  const [expanded, setExpanded] = useState(false);

  const handleToggle = (event: SyntheticEvent<HTMLDetailsElement>) => {
    setExpanded(event.currentTarget.open);
  };

  return (
    <details className="chart-data-disclosure" onToggle={handleToggle}>
      <summary role="button" aria-expanded={expanded}>{summary}</summary>
      {expanded && (
        <div className="chart-data-table-wrap">
          {rows.length ? (
            <table className="chart-data-table">
              <caption>{label}</caption>
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th key={column.key} scope="col">{column.header}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, rowIndex) => (
                  <tr key={getRowKey(row, rowIndex)}>
                    {columns.map((column) => {
                      const content = column.render(row);
                      const dataLabel = column.dataLabel ?? (typeof column.header === 'string' ? column.header : '');
                      return column.rowHeader ? (
                        <th key={column.key} scope="row" data-label={dataLabel}>{content}</th>
                      ) : (
                        <td key={column.key} data-label={dataLabel}>{content}</td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="chart-data-empty">{emptyMessage}</p>
          )}
        </div>
      )}
    </details>
  );
}
