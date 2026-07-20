import { useCurrentCompany } from "@/hooks/useCurrentCompany"
import { useFrappeGetDocList } from "frappe-react-sdk"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { formatCurrency } from "@/lib/numbers"
import { formatDate } from "@/lib/date"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { AlertCircle, Plus, ExternalLink, Search } from "lucide-react"
import ErrorBanner from "@/components/ui/error-banner"
import _ from "@/lib/translate"
import { slug } from "@/lib/frappe"
import { useState } from "react"
import { Input } from "@/components/ui/input"

interface MintBankTransfer {
    name: string
    date: string
    company: string
    from_bank_account: string
    to_bank_account: string
    reference_number: string
    amount: number
    status: string
    reconciliation_status: string
    description: string
    docstatus: number
}

const STATUS_LABELS: Record<string, string> = {
    "Draft": "Borrador",
    "Submitted": "Validado",
    "Cancelled": "Cancelado",
}

import { useAtomValue } from "jotai"
import { selectedBankAccountAtom } from "./bankRecAtoms"

const BankTransferList = () => {
    const companyID = useCurrentCompany()
    const bankAccount = useAtomValue(selectedBankAccountAtom)
    const [searchTerm, setSearchTerm] = useState("")

    const { data, error, isLoading } = useFrappeGetDocList<MintBankTransfer>("Mint Bank Transfer", {
        fields: [
            "name", "date", "company", "from_bank_account",
            "to_bank_account", "reference_number", "amount",
            "status", "reconciliation_status", "description", "docstatus"
        ],
        filters: companyID ? [["company", "=", companyID]] : [],
        orFilters: bankAccount ? [
            ["from_bank_account", "=", bankAccount.name],
            ["to_bank_account", "=", bankAccount.name]
        ] : [],
        orderBy: { field: "date", order: "desc" },
        limit_page_length: 100,
    })

    const openNewTransfer = () => {
        window.open("/app/mint-bank-transfer/new", "_blank")
    }

    const openTransfer = (name: string) => {
        window.open(`/app/${slug("Mint Bank Transfer")}/${name}`, "_blank")
    }

    const filteredData = data?.filter((row) => {
        const searchLower = searchTerm.toLowerCase();
        return (
            row.name.toLowerCase().includes(searchLower) ||
            row.reference_number?.toLowerCase().includes(searchLower) ||
            row.from_bank_account?.toLowerCase().includes(searchLower) ||
            row.to_bank_account?.toLowerCase().includes(searchLower) ||
            row.status?.toLowerCase().includes(searchLower) ||
            (STATUS_LABELS[row.status] || row.status).toLowerCase().includes(searchLower) ||
            row.reconciliation_status?.toLowerCase().includes(searchLower)
        );
    });

    return <div className="space-y-4 py-2">
        <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
                {_("Lista de transferencias bancarias de la empresa seleccionada.")}
            </p>
            <Button onClick={openNewTransfer} size="sm">
                <Plus className="mr-1 h-4 w-4" />
                {_("Nueva Transferencia")}
            </Button>
        </div>

        {data && data.length > 0 && (
            <div className="flex items-center space-x-2">
                <div className="relative w-full max-w-sm">
                    <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder={_("Buscar por ID, Referencia, Cuenta, o Estado...")}
                        className="pl-9"
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                    />
                </div>
            </div>
        )}

        {error && <ErrorBanner error={error} />}

        {isLoading && <div className="text-sm text-muted-foreground py-8 text-center">{_("Cargando...")}</div>}

        {data && data.length > 0 && (
            <div className="rounded-md border">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>{_("ID Transferencia")}</TableHead>
                            <TableHead>{_("Fecha")}</TableHead>
                            <TableHead>{_("Cuenta Bancaria Origen")}</TableHead>
                            <TableHead>{_("Cuenta Bancaria Destino")}</TableHead>
                            <TableHead>{_("Referencia")}</TableHead>
                            <TableHead className="text-right">{_("Monto")}</TableHead>
                            <TableHead>{_("Estado")}</TableHead>
                            <TableHead></TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {filteredData?.map((row) => (
                            <TableRow key={row.name}>
                                <TableCell className="font-medium">
                                    <button
                                        className="underline underline-offset-4 text-primary"
                                        onClick={() => openTransfer(row.name)}
                                    >
                                        {row.name}
                                    </button>
                                </TableCell>
                                <TableCell>{formatDate(row.date)}</TableCell>
                                <TableCell>{row.from_bank_account}</TableCell>
                                <TableCell>{row.to_bank_account}</TableCell>
                                <TableCell>{row.reference_number}</TableCell>
                                <TableCell className="text-right">{formatCurrency(row.amount)}</TableCell>
                                <TableCell>
                                    {row.status === "Submitted" ? (
                                        <Badge variant={
                                            row.reconciliation_status === "Conciliado" ? "default" :
                                            row.reconciliation_status === "Parcialmente Conciliado" ? "secondary" :
                                            "outline"
                                        } className={
                                            row.reconciliation_status === "Conciliado" ? "bg-emerald-500 hover:bg-emerald-600" :
                                            row.reconciliation_status === "Parcialmente Conciliado" ? "bg-yellow-500 hover:bg-yellow-600 text-white" :
                                            ""
                                        }>
                                            {row.reconciliation_status || "No Conciliado"}
                                        </Badge>
                                    ) : (
                                        <Badge variant={row.status === "Cancelled" ? "destructive" : "outline"}>
                                            {STATUS_LABELS[row.status] || row.status}
                                        </Badge>
                                    )}
                                </TableCell>
                                <TableCell>
                                    <Button variant="ghost" size="icon" onClick={() => openTransfer(row.name)}>
                                        <ExternalLink className="h-4 w-4" />
                                    </Button>
                                </TableCell>
                            </TableRow>
                        ))}
                        {filteredData?.length === 0 && (
                            <TableRow>
                                <TableCell colSpan={9} className="text-center py-6 text-muted-foreground">
                                    {_("No hay resultados para la búsqueda.")}
                                </TableCell>
                            </TableRow>
                        )}
                    </TableBody>
                </Table>
            </div>
        )}

        {data && data.length === 0 && (
            <Alert variant="default">
                <AlertCircle />
                <AlertTitle>{_("No se encontraron transferencias")}</AlertTitle>
                <AlertDescription>
                    {_("No hay transferencias bancarias registradas para la empresa seleccionada. Haz clic en 'Nueva Transferencia' para crear una.")}
                </AlertDescription>
            </Alert>
        )}
    </div>
}

export default BankTransferList
