import React, { useEffect, useState } from 'react';
import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  IconButton,
  Typography,
  Tabs,
  Tab,
  FormControlLabel,
  Switch,
} from '@mui/material';
import {
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
} from '@mui/icons-material';
import type { FinancialStatementResponse, FinancialStatementItem } from '../../lib/services/financial';
interface FinancialStatementsTableProps {
  data: FinancialStatementResponse;
}

interface ExpandedState {
  [itemId: number]: boolean;
}

const expandAllItems = (data: FinancialStatementResponse): ExpandedState =>
  Object.fromEntries(
    data.statements.flatMap((statement) =>
      statement.items.map((item) => [item.item_id, true]),
    ),
  );

export const FinancialStatementsTable: React.FC<FinancialStatementsTableProps> = ({ data }) => {
  const [selectedStatement, setSelectedStatement] = useState(0);
  const [expanded, setExpanded] = useState<ExpandedState>(() => expandAllItems(data));
  const [excludeAllZeroRows, setExcludeAllZeroRows] = useState(false);

  const statements = data.statements;
  const periods = data.periods;

  useEffect(() => {
    setExpanded(expandAllItems(data));
  }, [data]);

  const toggleExpanded = (itemId: number) => {
    setExpanded(prev => ({
      ...prev,
      [itemId]: !prev[itemId]
    }));
  };

  const formatValue = (value: number | null): string => {
    if (value === null || value === undefined) return '-';
    return new Intl.NumberFormat('vi-VN', { 
      minimumFractionDigits: 1,
      maximumFractionDigits: 1 
    }).format(value);
  };

  const getIndentation = (level: number): number => {
    return (level - 1) * 20;
  };

  const hasChildren = (item: FinancialStatementItem, allItems: FinancialStatementItem[]): boolean => {
    return allItems.some(otherItem => otherItem.parent_item_id === item.item_id);
  };

  const isAllZeroRow = (item: FinancialStatementItem): boolean => {
    return periods.length > 0 && periods.every((period) => item.values[period.label] === 0);
  };

  const getVisibleItems = (items: FinancialStatementItem[]): FinancialStatementItem[] => {
    const result: FinancialStatementItem[] = [];
    
    const addItemAndChildren = (item: FinancialStatementItem, shouldShow: boolean) => {
      const isExcluded = excludeAllZeroRows && isAllZeroRow(item);
      if (shouldShow && !isExcluded) {
        result.push(item);
      }
      
      // When a zero-only parent is hidden, promote its children so non-zero
      // descendants do not disappear with it.
      const showChildren = shouldShow && (isExcluded || expanded[item.item_id] || item.level === 1);
      const children = items.filter(child => child.parent_item_id === item.item_id);
      
      children
        .sort((a, b) => (a.display_order || 0) - (b.display_order || 0))
        .forEach(child => addItemAndChildren(child, showChildren));
    };

    // Start with top-level items
    items
      .filter(item => !item.parent_item_id)
      .sort((a, b) => (a.display_order || 0) - (b.display_order || 0))
      .forEach(item => addItemAndChildren(item, true));

    return result;
  };

  const renderTableRow = (item: FinancialStatementItem, allItems: FinancialStatementItem[]) => {
    const itemHasChildren = hasChildren(item, allItems);
    const isExpanded = expanded[item.item_id];
    const indentation = getIndentation(item.level);

    return (
      <TableRow 
        key={item.item_id}
        sx={{
          '&:hover': { backgroundColor: 'action.hover' },
          backgroundColor: item.level === 1 ? 'action.selected' : 'inherit'
        }}
      >
        <TableCell 
          sx={{ 
            paddingLeft: `${16 + indentation}px`,
            borderRight: 1,
            borderRightColor: 'line.subtle',
            minWidth: 300,
            fontWeight: item.level <= 2 ? 600 : 400,
            fontSize: item.level === 1 ? '0.95rem' : '0.875rem',
            color: item.level === 1 ? 'primary.main' : 'text.primary'
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            {itemHasChildren ? (
              <IconButton
                size="small"
                onClick={() => toggleExpanded(item.item_id)}
                sx={{ p: 0.5 }}
              >
                {isExpanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
              </IconButton>
            ) : (
              <Box sx={{ width: 24 }} />
            )}
            
            <Typography
              variant="body2"
              sx={{
                fontWeight: 'inherit',
                fontSize: 'inherit',
                color: 'inherit'
              }}
            >
              {item.title_vi}
            </Typography>
          </Box>
        </TableCell>
        
        {periods.map((period) => (
          <TableCell
            key={period.label}
            align="right"
            sx={{
              borderRight: 1,
            borderRightColor: 'line.subtle',
              fontFamily: 'monospace',
              fontSize: '0.875rem',
              fontWeight: item.level <= 2 ? 600 : 400,
              color: item.level === 1 ? 'primary.main' : 'text.primary',
              backgroundColor: item.level === 1 ? 'action.selected' : 'inherit'
            }}
          >
            {formatValue(item.values[period.label])}
          </TableCell>
        ))}
      </TableRow>
    );
  };

  if (!statements || statements.length === 0) {
    return (
      <Box sx={{ p: 3, textAlign: 'center', border: '1px dashed', borderColor: 'divider' }}>
        <Typography variant="h6" color="text.secondary" gutterBottom>
          Không có dữ liệu báo cáo tài chính
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Company: {data.company_ticker} - {data.company_name}<br/>
          Available statements: {data.statements?.length || 0}<br/>
          Available periods: {data.periods?.length || 0}
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ width: '100%' }}>
      {/* Statement Type Tabs */}
      <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}>
        <Tabs 
          value={selectedStatement} 
          onChange={(_, newValue) => setSelectedStatement(newValue)}
          variant="scrollable"
          scrollButtons="auto"
        >
          {statements.map((statement) => (
            <Tab 
              key={statement.statement_type} 
              label={statement.title} 
              sx={{
                textTransform: 'none',
                fontSize: '0.9rem',
                fontWeight: 500
              }}
            />
          ))}
        </Tabs>
      </Box>

      {/* Company Info */}
      <Box sx={{ mb: 2, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2 }}>
        <Box>
          <Typography variant="h6" gutterBottom>
            {data.company_ticker} - {data.company_name}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {statements[selectedStatement]?.title}
          </Typography>
        </Box>
        <FormControlLabel
          control={(
            <Switch
              checked={excludeAllZeroRows}
              onChange={(event) => setExcludeAllZeroRows(event.target.checked)}
            />
          )}
          label="Hide rows with all zero values"
        />
      </Box>

      {/* Financial Table */}
      <TableContainer 
        component={Paper} 
        sx={{ 
          maxHeight: '70vh',
          border: 1,
          borderColor: 'line.subtle',
          '& .MuiTableCell-root': {
            borderBottom: 1,
            borderBottomColor: 'line.subtle',
          }
        }}
      >
        <Table stickyHeader size="small">
          <TableHead>
            <TableRow>
              <TableCell 
                sx={{ 
                  backgroundColor: 'primary.main',
                  color: 'white',
                  fontWeight: 600,
                  borderRight: 1,
                  borderRightColor: 'line.subtle',
                  minWidth: 300
                }}
              >
                Chỉ tiêu
              </TableCell>
              {periods.map((period) => (
                <TableCell
                  key={period.label}
                  align="center"
                  sx={{
                    backgroundColor: 'primary.main',
                    color: 'white',
                    fontWeight: 600,
                    borderRight: 1,
                  borderRightColor: 'line.subtle',
                    minWidth: 120
                  }}
                >
                  {period.label}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {statements[selectedStatement] && 
              getVisibleItems(statements[selectedStatement].items).map((item) => 
                renderTableRow(item, statements[selectedStatement].items)
              )
            }
          </TableBody>
        </Table>
      </TableContainer>

      {/* Legend */}
      <Box sx={{ mt: 2 }}>
        <Typography variant="caption" color="text.secondary">
          * Đơn vị: triệu VND
        </Typography>
      </Box>
    </Box>
  );
};
