'use client';

import React, { useRef, useCallback, useState, useMemo } from 'react';
import { AgGridReact } from 'ag-grid-react';
import {
  AllCommunityModule,
  ModuleRegistry,
  type ColDef,
  type CellClickedEvent,
  type GridReadyEvent,
} from 'ag-grid-community';
import { Modal } from '@/components/ui/Modal';

ModuleRegistry.registerModules([AllCommunityModule]);

const PAGE_SIZE = 10;

interface GrantsAgGridProps {
  rowData: object[];
  columnDefs: ColDef[];
  quickFilterText?: string;
  loading?: boolean;
}

export function GrantsAgGrid({ rowData, columnDefs, quickFilterText, loading }: GrantsAgGridProps) {
  const gridRef = useRef<AgGridReact>(null);
  const [cellDetail, setCellDetail] = useState<{ column: string; value: unknown } | null>(null);

  const defaultColDef: ColDef = useMemo(
    () => ({
      sortable: true,
      filter: false,
      resizable: true,
      autoHeight: false,
      wrapText: false,
      suppressMovable: true,
      cellStyle: { cursor: 'pointer' },
    }),
    []
  );

  const handleCellClicked = useCallback((event: CellClickedEvent) => {
    const columnHeader = event.colDef.headerName ?? event.colDef.field ?? 'Value';
    const value =
      event.value != null
        ? typeof event.value === 'object'
          ? JSON.stringify(event.value, null, 2)
          : String(event.value)
        : null;
    setCellDetail({ column: columnHeader, value });
  }, []);

  const onGridReady = useCallback((params: GridReadyEvent) => {
    params.api.sizeColumnsToFit();
  }, []);

  return (
    <>
      {/* ~42px per row × 10 rows + 40px header + 56px pagination bar */}
      <div className="ag-theme-alpine w-full" style={{ height: '536px' }}>
        <AgGridReact
          ref={gridRef}
          rowData={rowData}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          pagination={true}
          paginationPageSize={PAGE_SIZE}
          paginationPageSizeSelector={[10, 25, 50]}
          loading={loading}
          quickFilterText={quickFilterText}
          onCellClicked={handleCellClicked}
          onGridReady={onGridReady}
          enableCellTextSelection={true}
          ensureDomOrder={true}
          suppressCellFocus={true}
          rowHeight={42}
          headerHeight={40}
          animateRows={true}
        />
      </div>

      <Modal
        open={cellDetail !== null}
        onClose={() => setCellDetail(null)}
        title={cellDetail?.column}
      >
        <div className="whitespace-pre-wrap break-words text-sm text-zinc-700">
          {cellDetail?.value != null ? (
            String(cellDetail.value)
          ) : (
            <span className="text-zinc-400 italic">Empty</span>
          )}
        </div>
      </Modal>
    </>
  );
}
