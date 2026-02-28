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
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';

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
  const [cellDetailOpen, setCellDetailOpen] = useState(false);

  const defaultColDef: ColDef = useMemo(
    () => ({
      sortable: true,
      filter: false, // We use the quick filter instead
      resizable: true,
      autoHeight: false,
      wrapText: false,
      suppressMovable: true,
    }),
    []
  );

  const handleCellClicked = useCallback((event: CellClickedEvent) => {
    const columnHeader = event.colDef.headerName ?? event.colDef.field ?? 'Value';
    // Get a plain text representation of the value
    const value =
      event.value != null
        ? typeof event.value === 'object'
          ? JSON.stringify(event.value)
          : String(event.value)
        : null;
    setCellDetail({ column: columnHeader, value });
    setCellDetailOpen(true);
  }, []);

  const onGridReady = useCallback((params: GridReadyEvent) => {
    params.api.sizeColumnsToFit();
  }, []);

  return (
    <>
      <div
        className="ag-theme-alpine w-full"
        style={{ height: `${PAGE_SIZE * 42 + 112}px` }} // ~42px per row + header + pagination
      >
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
          suppressRowHoverHighlight={false}
          animateRows={true}
        />
      </div>

      <Dialog open={cellDetailOpen} onOpenChange={setCellDetailOpen}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-auto">
          <DialogHeader>
            <DialogTitle>{cellDetail?.column}</DialogTitle>
          </DialogHeader>
          <div className="mt-4 whitespace-pre-wrap break-words text-sm">
            {cellDetail?.value != null ? (
              String(cellDetail.value)
            ) : (
              <span className="text-muted-foreground italic">Empty</span>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
