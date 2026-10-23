import assert from 'node:assert/strict';
import test from 'node:test';
import {serializeTSV} from '../src/library/tsv.js';

test('TSV preserves scalar values, empty null cells, and header/record order', () => {
  assert.equal(serializeTSV(['Text','Number','Flag','Null','Missing'], [['中文',0,false,null,undefined],['second',-3,true,'','']]),
    'Text\tNumber\tFlag\tNull\tMissing\r\n中文\t0\tfalse\t\t\r\nsecond\t-3\ttrue\t\t');
});

test('TSV quotes tab, CR/LF and double quotes without creating extra cells', () => {
  assert.equal(serializeTSV(['a\tb','line\nheader'], [['tab\tcell','CR\rLF\n"quoted"']]),
    '"a\tb"\t"line\nheader"\r\n"tab\tcell"\t"CR\rLF\n""quoted"""');
});

test('TSV represents objects and arrays as JSON text in quoted cells', () => {
  assert.equal(serializeTSV(['object','array'], [[{key:'tab\tline\n'},[1,'two',null]]]),
    'object\tarray\r\n"{""key"":""tab\\tline\\n""}"\t"[1,""two"",null]"');
});



test('spreadsheet text protection is opt-in and leaves numeric scalars unchanged', () => {
  const labels = ['=synthetic label', '+synthetic label', '-synthetic label', '@synthetic label', ' \t=synthetic label', '\tplain label', '\rplain label', '\u0000plain label', '\ufeff@synthetic label'];
  for (const label of labels) {
    const guarded = serializeTSV(['Text'], [[label]], {spreadsheetTextColumns: [0]});
    const expected = "'" + label;
    assert.equal(guarded, 'Text\r\n' + (/[\t\r\n"]/.test(expected) ? `"${expected.replaceAll('"', '""')}"` : expected));
    assert.equal(serializeTSV(['Text'], [[label]]), 'Text\r\n' + (/[\t\r\n"]/.test(label) ? `"${label.replaceAll('"', '""')}"` : label), 'legacy callers retain original bytes');
  }
  assert.equal(serializeTSV(['Number','Flag','Null','Text'], [[-3,false,null,' ordinary label']], {spreadsheetTextColumns: [0,1,2,3]}), 'Number\tFlag\tNull\tText\r\n-3\tfalse\t\t ordinary label');
});
