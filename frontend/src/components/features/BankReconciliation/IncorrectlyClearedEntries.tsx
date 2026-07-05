import { useAtomValue } from "jotai"
import { MissingFiltersBanner } from "./MissingFiltersBanner"
import { bankRecDateAtom, selectedBankAccountAtom } from "./bankRecAtoms"
import { useCurrentCompany } from "@/hooks/useCurrentCompany"
import { Paragraph } from "@/components/ui/typography"
import { useMemo } from "react"
import { useFrappeGetCall, useFrappePostCall } from "frappe-react-sdk"
import { QueryReportReturnType } from "@/types/custom/Reports"
import { formatDate } from "@/lib/date"
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { formatCurrency } from "@/lib/numbers"
import { getCompanyCurrency } from "@/lib/company"
import { getErrorMessage, slug } from "@/lib/frappe"
import { Button } from "@/components/ui/button"
import { toast } from "sonner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { PartyPopper } from "lucide-react"
import ErrorBanner from "@/components/ui/error-banner"
import _ from "@/lib/translate"

const IncorrectlyClearedEntries = () => {
    const companyID = useCurrentCompany()
    const bankAccount = useAtomValue(selectedBankAccountAtom)
    const dates = useAtomValue(bankRecDateAtom)

    if (!companyID || !bankAccount || !dates) {
        const missingFields = []
        if (!companyID) {
            missingFields.push('Company')
        }
        if (!bankAccount) {
            missingFields.push('Cuenta Bancaria')
        }
        if (!dates) {
            missingFields.push('Dates')
        }
        return <MissingFiltersBanner text={`Por favor selecciona ${missingFields.join(', ')} para ver las entradas liquidadas incorrectamente.`} />
    }

    return <IncorrectlyClearedEntriesView />
}

interface IncorrectlyClearedEntry {
    payment_document: string
    payment_entry: string
    debit: number
    credit: number
    posting_date: string,
    clearance_date: string,
}

const IncorrectlyClearedEntriesView = () => {

    const companyID = useCurrentCompany()
    const bankAccount = useAtomValue(selectedBankAccountAtom)
    const dates = useAtomValue(bankRecDateAtom)

    const filters = useMemo(() => {
        return JSON.stringify({
            company: companyID,
            account: bankAccount?.account,
            report_date: dates.toDate
        })
    }, [companyID, bankAccount, dates])

    const { data, error, mutate } = useFrappeGetCall<{ message: QueryReportReturnType<IncorrectlyClearedEntry> }>('frappe.desk.query_report.run', {
        report_name: 'Cheques and Deposits Incorrectly cleared',
        filters,
        ignore_prepared_report: 1,
        are_default_filters: false,
    }, `Report-Cheques and Deposits Incorrectly cleared-${filters}`, { keepPreviousData: true, revalidateOnFocus: false }, 'POST')

    const formattedToDate = formatDate(dates.toDate)

    const { call: clearClearingDate } = useFrappePostCall('mint.apis.bank_reconciliation.clear_clearing_date')

    const onClearClick = (voucher_type: string, voucher_name: string) => {
        clearClearingDate({ voucher_type, voucher_name })
            .then(() => {
                toast.success(_('Cleared'), {
                    duration: 1000
                })
                mutate()
            }).catch((e) => {
                toast.error(_("There was an error while performing the action."), {
                    description: getErrorMessage(e),
                    duration: 5000
                })
            })
    }

    return <div className="space-y-4 py-2">

        <div>
            <Paragraph className="text-sm">
                <span dangerouslySetInnerHTML={{
                    __html: _("Este reporte muestra todas las entradas en el sistema donde la <strong>fecha de liquidación es anterior a la fecha de contabilización</strong>, lo cual es incorrecto.")
                }} />
                <br />
                {data && data.message.result.length > 0 && <span>
                    <span dangerouslySetInnerHTML={{
                        __html: _("Las siguientes entradas tienen una fecha de contabilización posterior al {0} pero la fecha de liquidación es anterior al {1}.", [`<strong>${formattedToDate}</strong>`, `<strong>${formattedToDate}</strong>`])
                    }} />
                    <br />
                    {_("Puedes restablecer las fechas de liquidación de estas entradas aquí.")}
                </span>}
            </Paragraph>
        </div>

        {error && <ErrorBanner error={error} />}

        {data && data.message.result.length > 0 &&
            <Table>
                <TableCaption>{_("Entradas liquidadas incorrectamente según el reporte.")}</TableCaption>
                <TableHeader>
                    <TableRow>
                        <TableHead className="w-[100px]">{_("Tipo de Documento")}</TableHead>
                        <TableHead>{_("Documento de Pago")}</TableHead>
                        <TableHead className="text-right">{_("Débito")}</TableHead>
                        <TableHead className="text-right">{_("Crédito")}</TableHead>
                        <TableHead>{_("Fecha de Contabilización")}</TableHead>
                        <TableHead>{_("Fecha de Liquidación")}</TableHead>
                        <TableHead>{_("Acciones")}</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {data.message.result.map((row: IncorrectlyClearedEntry) => (
                        <TableRow key={row.payment_entry}>
                            <TableCell>{_(row.payment_document)}</TableCell>
                            <TableCell><a target="_blank" className="underline underline-offset-4" href={`/app/${slug(row.payment_document)}/${row.payment_entry}`}>{row.payment_entry}</a></TableCell>
                            <TableCell className="text-right">{formatCurrency(row.debit, bankAccount?.account_currency ?? getCompanyCurrency(companyID))}</TableCell>
                            <TableCell className="text-right">{formatCurrency(row.credit, bankAccount?.account_currency ?? getCompanyCurrency(companyID))}</TableCell>
                            <TableCell>{formatDate(row.posting_date)}</TableCell>
                            <TableCell>{formatDate(row.clearance_date)}</TableCell>
                            <TableCell>
                                <Button
                                    variant='link'
                                    size="sm"
                                    className="text-destructive px-0"
                                    onClick={() => onClearClick(row.payment_document, row.payment_entry)}>{_("Restablecer Fecha de Liquidación")}</Button>
                            </TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>}

        {data && data.message.result.length === 0 &&
            <Alert variant='default'>
                <PartyPopper />
                <AlertTitle>{_("¡Todo está bien!")}</AlertTitle>
                <AlertDescription>
                    {_("No hay entradas en el sistema donde la fecha de liquidación sea anterior a la fecha de contabilización.")}
                </AlertDescription>
            </Alert>
        }


    </div>
}

export default IncorrectlyClearedEntries
