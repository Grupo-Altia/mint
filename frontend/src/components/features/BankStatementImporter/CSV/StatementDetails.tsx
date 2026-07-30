import _ from '@/lib/translate'
import { GetStatementDetailsResponse } from '../import_utils'
import { flt, formatCurrency } from '@/lib/numbers'
import { formatDate } from '@/lib/date'
import { bankRecDateAtom, SelectedBank } from '../../BankReconciliation/bankRecAtoms'
import { ChevronLeftIcon, ExternalLinkIcon, InfoIcon, Landmark, Loader2Icon, ChevronDownIcon, ChevronUpIcon } from 'lucide-react'
import { H2, H3, H4, Paragraph } from '@/components/ui/typography'
import { FileTypeIcon } from '@/components/ui/file-dropzone'
import { getFileExtension } from '@/lib/file'
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Separator } from '@/components/ui/separator'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useFrappeEventListener, useFrappePostCall } from 'frappe-react-sdk'
import { toast } from 'sonner'
import ErrorBanner from '@/components/ui/error-banner'
import { useNavigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { Progress } from '@/components/ui/progress'
import { useSetAtom } from 'jotai'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

/** Serialización estable del mapeo de columnas: JSON.stringify respeta el orden de
 *  inserción de las claves, así que el mismo mapeo con las claves en otro orden comparaba
 *  distinto y dejaba "Aplicar" habilitado aunque no hubiera nada nuevo que aplicar. */
const serializeMapping = (mapping: Record<string, number>) =>
    JSON.stringify(Object.fromEntries(Object.entries(mapping).sort(([a], [b]) => a.localeCompare(b))))

const AMOUNT_FORMAT_LABEL_MAP = {
    "separate_columns_for_withdrawal_and_deposit": _("Separate columns for withdrawal and deposit"),
    "dr_cr_in_amount": _('Amount column has "CR"/"DR" values'),
    "positive_negative_in_amount": _("Amount column has positive/negative values"),
    "cr_dr_in_transaction_type": _('Transaction type column has "CR"/"DR" values'),
    "deposit_withdrawal_in_transaction_type": _('Transaction type column has "Deposit"/"Withdrawal" values'),
    "positive_negative_in_transaction_type": _('Transaction type column has "+/-" values'),
}

const parseDateFormat = (dateFormat: string) => {

    const charMap = {
        "%d": "DD",
        "%m": "MM",
        "%Y": "YYYY",
        "%y": "YY",
        "%b": "MMM",
        "%B": "MMMM",
    }

    let label = dateFormat

    Object.keys(charMap).forEach((char) => {
        label = label.replace(char, charMap[char as keyof typeof charMap])
    })

    return {
        label,
        dayjsFormat: label,
    }


}

type Props = {
    data: GetStatementDetailsResponse,
    bank: SelectedBank | null,
    onBack: () => void,
    customMapping?: string,
    setCustomMapping?: (val: string | undefined) => void
}

const StatementDetails = ({ data, bank, onBack, customMapping, setCustomMapping }: Props) => {
    const dateFormatMeta = parseDateFormat(data.date_format)

    const skippedByRule = data.skipped_by_ignore_rule ?? 0
    const skippedBeforeOpeningDate = data.skipped_before_opening_date ?? 0

    const { call, loading, error } = useFrappePostCall<{ message: { success: boolean, start_date: string, end_date: string } }>('mint.apis.statement_import.import_statement')

    const navigate = useNavigate()
    const [showMapping, setShowMapping] = useState(false)

    const setDates = useSetAtom(bankRecDateAtom)

    const onImport = () => {

        call({
            file_url: data.file_path,
            bank_account: bank?.name,
            custom_mapping: customMapping
        }).then((response) => {
            if (response.message.start_date && response.message.end_date) {
                setDates({
                    fromDate: response.message.start_date,
                    toDate: response.message.end_date,
                })
            }
            toast.success(_("Bank statement imported."))
            navigate(`/`)
        }).catch(() => {
            toast.error(_("There was an error while importing the bank statement."))
        })

    }

    const [progress, setProgress] = useState(0)
    const [imgError, setImgError] = useState(false)
    const [localMapping, setLocalMapping] = useState<Record<string, number>>(data.column_mapping || {})

    // useState solo toma el valor inicial: sin esto los selectores quedan mostrando el
    // mapeo de la primera respuesta aunque el backend devuelva otro (al aplicar un mapeo,
    // o al reabrir el importador con otro archivo).
    useEffect(() => {
        setLocalMapping(data.column_mapping || {})
    }, [data.column_mapping])

    useFrappeEventListener("mint-statement-import-progress", (event) => {
        setProgress(event.progress)
    })

    return (
        <div className='flex flex-col gap-4'>
            <div className='flex flex-col gap-4'>
                <div className='flex justify-between items-center'>
                    <Button size='sm' variant='outline' onClick={onBack}>
                        <ChevronLeftIcon />
                        {_("Back")}
                    </Button>
                    <Button onClick={onImport} disabled={loading} size='sm' type='button'>
                        {loading ? <Loader2Icon className='size-4 animate-spin' /> : null}
                        {loading ? _("Importing...") : _("Import {0} transactions", [data.final_transactions?.length?.toString() || "0"])}</Button>
                </div>
                <div className='flex items-start gap-4'>
                    <div className='flex flex-col gap-1'>
                        <H2 className='text-lg border-0 p-0'>{_("Detalles del Estado de Cuenta")}</H2>
                        <Paragraph className='text-sm'><span>
                            {_("Hemos autodetectado los detalles del archivo.")}
                        </span><br />
                            <span>
                                {_("Por favor revisa los detalles a continuación y haz clic en 'Importar' para proceder.")}
                            </span>
                        </Paragraph>
                    </div>
                </div>

                {progress > 0 && <div className='flex flex-col gap-2'><Progress value={progress} max={100} />
                    <span className='text-sm'>{_("Importing {0} transactions", [progress.toString()])}
                    </span>
                </div>}

                {error && <ErrorBanner error={error} />}

                <Table>
                    <TableBody>
                        <TableRow>
                            <TableHead className='bg-muted/70'>{_("Cuenta Bancaria")}</TableHead>
                            <TableCell>
                                <div className='flex items-center gap-2'>
                                    {bank?.logo && !imgError ? <img
                                        src={`/assets/mint/mint/${bank.logo}`}
                                        alt={bank.bank || bank.name || ''}
                                        onError={() => setImgError(true)}
                                        className="max-w-24 object-left h-8 object-contain"
                                    /> : <div className="rounded-md flex items-center h-8 gap-2">
                                        <Landmark size={'30px'} />
                                        <H4 className="text-base mb-0">{bank?.bank}</H4>
                                    </div>}
                                    <span className="tracking-tight text-sm font-medium">{bank?.account_name}</span>
                                    <span title="GL Account" className="text-sm">{bank?.account}</span>
                                </div>
                            </TableCell>
                        </TableRow>
                        <TableRow>
                            <TableHead className='bg-muted/70'>{_("Archivo de Estado de Cuenta")}</TableHead>
                            <TableCell>
                                <div className='flex items-center gap-2'>
                                    <FileTypeIcon fileType={getFileExtension(data.file_name)} size='md' showBackground={false} />
                                    {data.file_name}
                                </div>
                            </TableCell>
                        </TableRow>
                        <TableRow>
                            <TableHead className='bg-muted/70'>{_("Fechas de las Transacciones")}</TableHead>
                            <TableCell>{_("{0} al {1}", [formatDate(data.statement_start_date, "Do MMMM YYYY"), formatDate(data.statement_end_date, "Do MMMM YYYY")])}</TableCell>
                        </TableRow>
                        <TableRow>
                            <TableHead className='bg-muted/70'>{_("Número de Transacciones")}</TableHead>
                            <TableCell>{data.transaction_rows.length}</TableCell>
                        </TableRow>
                        <TableRow>
                            <TableHead className='bg-muted/70'>{_("Saldo de cierre al {}", [formatDate(data.statement_end_date, "Do MMMM YYYY")])}</TableHead>
                            <TableCell className='font-mono'>{formatCurrency(flt(data.closing_balance, 2))}</TableCell>
                        </TableRow>
                        <TableRow>
                            <TableHead className='bg-muted/70'>
                                <div className='flex items-center gap-2'>
                                    {_("Formato de Monto Detectado")} <Tooltip>
                                        <TooltipTrigger><InfoIcon size={16} /></TooltipTrigger>
                                        <TooltipContent>
                                            {_("El formato de monto detectado en el archivo. Se utiliza para identificar los valores de depósito y retiro de cada fila.")}
                                        </TooltipContent>
                                    </Tooltip>
                                </div>
                            </TableHead>
                            <TableCell>{AMOUNT_FORMAT_LABEL_MAP[data.amount_format as keyof typeof AMOUNT_FORMAT_LABEL_MAP]}</TableCell>
                        </TableRow>
                        <TableRow>
                            <TableHead className='bg-muted/70'>
                                <div className='flex items-center gap-2'>
                                    {_("Formato de Fecha Detectado")}
                                    <Tooltip>
                                        <TooltipTrigger><InfoIcon size={16} /></TooltipTrigger>
                                        <TooltipContent>
                                            {_("El formato de fecha detectado en el archivo. Se utiliza para identificar las fechas.")}
                                        </TooltipContent>
                                    </Tooltip>
                                </div>
                            </TableHead>
                            <TableCell>
                                {dateFormatMeta?.label || data.date_format} (e.g.{" "}
                                {formatDate(new Date(), dateFormatMeta?.dayjsFormat || "YYYY-MM-DD")})
                            </TableCell>
                        </TableRow>
                    </TableBody>
                </Table>
            </div>

            {/* Filas que el archivo trae pero no se van a importar. Sin esto el usuario ve
                menos filas en la vista previa y no tiene forma de saber si faltan 3 o 300. */}
            {(skippedByRule > 0 || skippedBeforeOpeningDate > 0) && (
                <div className='rounded-md border border-border bg-muted/40 p-3 flex flex-col gap-1'>
                    <Paragraph className='text-sm font-medium'>{_("Filas omitidas del archivo")}</Paragraph>
                    {skippedByRule > 0 && (
                        <Paragraph className='text-sm'>
                            {_("{0} por coincidir con una regla marcada como Ignorar Transacción.", [skippedByRule.toString()])}
                        </Paragraph>
                    )}
                    {skippedBeforeOpeningDate > 0 && (
                        <Paragraph className='text-sm'>
                            {_("{0} por ser anteriores a la Fecha de Inicio de Operaciones de la cuenta.", [skippedBeforeOpeningDate.toString()])}
                        </Paragraph>
                    )}
                </div>
            )}

            <Separator />
            <div className='flex flex-col gap-4'>
                <div 
                    className='flex flex-col gap-1 cursor-pointer select-none group'
                    onClick={() => setShowMapping(!showMapping)}
                >
                    <div className='flex items-center gap-2'>
                        <H3 className='text-base border-0 p-0 m-0'>{_("Mapeo Manual de Columnas")}</H3>
                        {showMapping ? (
                            <ChevronUpIcon className='h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors' />
                        ) : (
                            <ChevronDownIcon className='h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors' />
                        )}
                    </div>
                    <Paragraph className='text-sm'>{_("Ajusta manualmente qué columna corresponde a cada dato si la autodetección no fue correcta.")}</Paragraph>
                </div>
                
                {showMapping && (
                    <>
                        <Table>
                            <TableBody>
                                {["Date", "Amount", "Deposit", "Withdrawal", "Description", "Reference", "Transaction Type", "Balance"].map(field => (
                                    <TableRow key={field}>
                                        <TableHead className='bg-muted/70 align-middle w-1/3'>{_(field)}</TableHead>
                                        <TableCell>
                                            <Select 
                                                value={localMapping[field] !== undefined ? localMapping[field].toString() : "none"}
                                                onValueChange={(val) => {
                                                    setLocalMapping(prev => {
                                                        const newMapping = { ...prev }
                                                        if (val === "none") {
                                                            delete newMapping[field]
                                                        } else {
                                                            newMapping[field] = parseInt(val)
                                                        }
                                                        return newMapping
                                                    })
                                                }}
                                            >
                                                <SelectTrigger className="w-full">
                                                    <SelectValue placeholder={_("Seleccionar Columna")} />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="none">{_("No importar")}</SelectItem>
                                                    {data.columns.map(c => (
                                                        <SelectItem key={c.index} value={c.index.toString()}>
                                                            {c.header_text} (Col {c.index + 1})
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                        
                        <div className='flex justify-end'>
                            <Button 
                                size='sm' 
                                variant='secondary' 
                                onClick={() => setCustomMapping?.(serializeMapping(localMapping))}
                                disabled={serializeMapping(localMapping) === (customMapping ?? serializeMapping(data.column_mapping || {}))}
                            >
                                {_("Aplicar y Refrescar Vista Previa")}
                            </Button>
                        </div>
                    </>
                )}
            </div>

            {data.conflicting_transactions.length > 0 && <Separator />}

            {data.conflicting_transactions.length > 0 ? <div className='flex flex-col gap-4'>
                <div className='flex flex-col gap-1'>
                    <H3 className='text-base border-0 p-0'>{_("Transacciones en Conflicto")}</H3>
                    {data.conflicting_transactions.length === 1 ? (
                        <Paragraph className='text-sm'>{_("Hemos encontrado 1 transacción existente en el sistema que entra en conflicto con las transacciones del archivo. ¿Estás seguro de que deseas proceder con la importación?")}</Paragraph>
                    ) : (
                        <Paragraph className='text-sm'>{_("Hemos encontrado {0} transacciones existentes en el sistema que entran en conflicto con las transacciones del archivo. ¿Estás seguro de que deseas proceder con la importación?", [data.conflicting_transactions.length.toString()])}</Paragraph>
                    )}
                </div>
                <div className='max-h-[400px] overflow-scroll border border-border rounded-md pb-2'>
                    <Table>
                        <TableCaption>{_("Transacciones existentes en el sistema que pertenecen a la misma cuenta bancaria y al mismo rango de fechas")}</TableCaption>
                        <TableHeader>
                            <TableRow>
                                <TableHead>{_("Fecha")}</TableHead>
                                <TableHead>{_("Descripción")}</TableHead>
                                <TableHead>{_("Ref.")}</TableHead>
                                <TableHead className='text-right'>{_("Retiro")}</TableHead>
                                <TableHead className='text-right'>{_("Depósito")}</TableHead>

                                <TableHead></TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {data.conflicting_transactions.map((transaction) => (
                                <TableRow key={transaction.name}>
                                    <TableCell>{formatDate(transaction.date)}</TableCell>
                                    <TableCell>{transaction.description}</TableCell>
                                    <TableCell>{transaction.reference_number ? transaction.reference_number : "-"}</TableCell>
                                    <TableCell className='text-right font-mono'>{formatCurrency(transaction.withdrawal, transaction.currency)}</TableCell>
                                    <TableCell className='text-right font-mono'>{formatCurrency(transaction.deposit, transaction.currency)}</TableCell>
                                    <TableCell className='text-right'>
                                        <Tooltip>
                                            <TooltipTrigger asChild>
                                                <Button variant='link' size='icon' asChild className='text-muted-foreground hover:text-black p-0 h-4'>
                                                    <a href={`/app/bank-transaction/${transaction.name}`} target='_blank' rel='noopener noreferrer'>
                                                        <ExternalLinkIcon />
                                                    </a>
                                                </Button>
                                            </TooltipTrigger>
                                            <TooltipContent>
                                                {_("Abrir {0} en una nueva pestaña", [transaction.name])}
                                            </TooltipContent>
                                        </Tooltip>

                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>

            </div> : null}

            <Separator />

            <div className='flex flex-col gap-4'>
                <div className='flex flex-col gap-1'>
                    <H3 className='text-base border-0 p-0'>{_("Vista Previa de Transacciones")}</H3>
                    {data.final_transactions?.length === 1 ? (
                        <Paragraph className='text-sm'>{_("Hemos encontrado 1 transacción en el archivo que será importada al sistema. Por favor, revisa los detalles a continuación y haz clic en 'Importar' para proceder.")}</Paragraph>
                    ) : (
                        <Paragraph className='text-sm'>{_("{0} transacciones serán importadas al sistema. Por favor, revisa los detalles a continuación y haz clic en 'Importar' para proceder.", [data.final_transactions?.length?.toString() || "0"])}</Paragraph>
                    )}
                </div>
                <div className='max-h-[400px] overflow-scroll border border-border rounded-md pb-2'>
                    <Table>
                        <TableCaption>{_("Transacciones a ser importadas al sistema")}</TableCaption>
                        <TableHeader>
                            <TableRow>
                                <TableHead className='w-8'>#</TableHead>
                                <TableHead>{_("Fecha")}</TableHead>
                                <TableHead>{_("Descripción")}</TableHead>
                                <TableHead>{_("Ref.")}</TableHead>
                                <TableHead className='text-right'>{_("Retiro")}</TableHead>
                                <TableHead className='text-right'>{_("Depósito")}</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {data.final_transactions?.map((transaction, index) => (
                                <TableRow key={index}>
                                    <TableCell className='w-8'>{index + 1}</TableCell>
                                    <TableCell>{formatDate(transaction.date)}</TableCell>
                                    <TableCell className='max-w-[200px] w-fit overflow-hidden text-ellipsis'>{transaction.description}</TableCell>
                                    <TableCell className='max-w-[100px] w-fit overflow-hidden text-ellipsis'>{transaction.reference}</TableCell>
                                    <TableCell className='text-right font-mono'>{formatCurrency(transaction.withdrawal, data.currency)}</TableCell>
                                    <TableCell className='text-right font-mono'>{formatCurrency(transaction.deposit, data.currency)}</TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            </div>
        </div>

    )
}

export default StatementDetails