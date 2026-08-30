import React, { useState } from 'react';
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

export const FinancialStatementsTable: React.FC<FinancialStatementsTableProps> = ({ data }) => {
  const [selectedStatement, setSelectedStatement] = useState(0);
  const [expanded, setExpanded] = useState<ExpandedState>({});

  const statements = data.statements;
  const periods = data.periods;

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

  const getVisibleItems = (items: FinancialStatementItem[]): FinancialStatementItem[] => {
    const result: FinancialStatementItem[] = [];
    
    const addItemAndChildren = (item: FinancialStatementItem, shouldShow: boolean) => {
      if (shouldShow) {
        result.push(item);
      }
      
      // Add children if parent is expanded or if it's a top-level item
      const showChildren = shouldShow && (expanded[item.item_id] || item.level === 1);
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
          '&:hover': { backgroundColor: 'rgba(0, 0, 0, 0.04)' },
          backgroundColor: item.level === 1 ? 'rgba(25, 118, 210, 0.08)' : 'inherit'
        }}
      >
        <TableCell 
          sx={{ 
            paddingLeft: `${16 + indentation}px`,
            borderRight: '1px solid rgba(224, 224, 224, 1)',
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
              borderRight: '1px solid rgba(224, 224, 224, 1)',
              fontFamily: 'monospace',
              fontSize: '0.875rem',
              fontWeight: item.level <= 2 ? 600 : 400,
              color: item.level === 1 ? 'primary.main' : 'text.primary',
              backgroundColor: item.level === 1 ? 'rgba(25, 118, 210, 0.04)' : 'inherit'
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
      <Box sx={{ mb: 2 }}>
        <Typography variant="h6" gutterBottom>
          {data.company_ticker} - {data.company_name}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {statements[selectedStatement]?.title}
        </Typography>
      </Box>

      {/* Financial Table */}
      <TableContainer 
        component={Paper} 
        sx={{ 
          maxHeight: '70vh',
          border: '1px solid rgba(224, 224, 224, 1)',
          '& .MuiTableCell-root': {
            borderBottom: '1px solid rgba(224, 224, 224, 1)',
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
                  borderRight: '1px solid rgba(255, 255, 255, 0.3)',
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
                    borderRight: '1px solid rgba(255, 255, 255, 0.3)',
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
